"""The production steps: plan → generate (with self-check and retry) → cross-review → build."""

import json
from pathlib import Path
from typing import Literal

from pydantic import ValidationError

from dili.check import Report, check_exam
from dili.export import write_exam
from dili.models import Batch
from dili.models.common import Id, Model, Text

from . import agora
from .plan import allocate
from .runs import Catalog, Run
from .workorder import generation_order, review_order, revision_order

# Whole-exam findings that only make sense once every section has been delivered.
WHOLE_EXAM_CODES = {"section_batch_count", "numbering", "total_points"}


class ReviewFinding(Model):
    object_id: Id
    severity: Literal["ok", "minor", "major"]
    notes: Text


class ReviewFile(Model):
    reviewer: Text
    findings: tuple[ReviewFinding, ...]


def plan_run(run_id: str, blueprint_path: Path, catalog: Catalog) -> Run:
    run = Run.create(run_id, blueprint_path)
    blueprint = run.blueprint()
    plan = allocate(blueprint, catalog.scope(blueprint), seed=run_id)
    (run.root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for section_id, entry in plan.items():
        run.update_section(section_id, worker=entry["worker"], reviewer=entry["reviewer"], status="planned")
    run.make_current()
    return run


def validate_section(run: Run, section_id: str, catalog: Catalog) -> Report:
    """Check one delivered batch on its own, as far as that is possible without the rest of the exam."""
    report = Report()
    path = run.batch_path(section_id)
    if not path.exists():
        report.add("errors", "missing_batch", f"尚未交付：{path.as_posix()}")
        return report
    try:
        batch = Batch.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except ValidationError as exc:
        report.add("errors", "invalid_batch", f"JSON 格式或欄位錯誤：{exc}")
        return report
    if batch.section_id != section_id or batch.run_id != run.id:
        report.add("errors", "wrong_batch", f"run_id／section_id 應為 {run.id}／{section_id}")
    full = check_exam(run.blueprint(), [batch], lessons=catalog.lessons, wordlist=catalog.wordlist)
    for severity in ("errors", "warnings", "infos"):
        kept = [f for f in getattr(full, severity) if f.code not in WHOLE_EXAM_CODES]
        getattr(report, severity).extend(kept)
    report.pending_count = full.pending_count
    return report


def _errors_text(report: Report) -> str:
    quiet = Report(errors=report.errors, warnings=report.warnings)
    return quiet.render()


def generate_section(run: Run, section_id: str, catalog: Catalog, *, worker: str | None = None,
                     attempts: int = 3, log=print) -> bool:
    """Assign the section, wait, validate; feed the errors back to the same thread until clean."""
    entry = run.state()["sections"][section_id]
    worker = worker or entry["worker"]
    lessons = catalog.scope(run.blueprint())
    thread = entry.get("thread") or agora.new_thread(f"dili {run.id} 出題 {section_id}")
    run.update_section(section_id, worker=worker, thread=thread)
    previous = ""
    for attempt in range(1, attempts + 1):
        order = run.root / "workorders" / f"{section_id}-{attempt}.md"
        order.write_text(generation_order(run, section_id, lessons, worker=worker, previous_errors=previous),
                         encoding="utf-8")
        run.make_current()
        if agora.is_active(thread):
            # Resume: a previous process already assigned this attempt; just wait for it.
            log(f"[{section_id}] {thread} 已有進行中的接手，等待完成")
        else:
            run.update_section(section_id, status="generating", attempts=attempt)
            log(f"[{section_id}] 第 {attempt} 次派給 {worker}（{thread}）")
            agora.assign(thread, worker, order)
        final = agora.wait(thread)
        report = validate_section(run, section_id, catalog)
        log(f"[{section_id}] 接手方狀態 {final}；{report.render().splitlines()[0]}")
        if not report.errors:
            run.update_section(section_id, status="generated")
            return True
        previous = _errors_text(report)
    run.update_section(section_id, status="failed")
    return False


def review_section(run: Run, section_id: str, catalog: Catalog, *, reviewer: str | None = None,
                   log=print) -> bool:
    """A different AI reviews the batch; its notes are attached as AI review records (never approvals)."""
    entry = run.state()["sections"][section_id]
    reviewer = reviewer or entry["reviewer"]
    if reviewer == entry.get("worker"):
        raise ValueError("交叉審題必須由出題以外的 AI 負責")
    batch = run.load_batch(section_id, reviewed=False)
    if batch is None:
        raise ValueError(f"{section_id} 尚未出題")
    thread = entry.get("review_thread") or agora.new_thread(f"dili {run.id} 審題 {section_id}")
    run.update_section(section_id, reviewer=reviewer, review_thread=thread, review_status="reviewing")
    order = run.root / "workorders" / f"{section_id}-review.md"
    order.write_text(review_order(run, section_id, catalog.scope(run.blueprint()), reviewer=reviewer),
                     encoding="utf-8")
    if not agora.is_active(thread):
        log(f"[{section_id}] 交叉審題派給 {reviewer}（{thread}）")
        agora.assign(thread, reviewer, order)
    agora.wait(thread)
    try:
        review = ReviewFile.model_validate_json(run.review_path(section_id).read_text(encoding="utf-8-sig"))
    except (OSError, ValidationError) as exc:
        run.update_section(section_id, review_status="failed")
        log(f"[{section_id}] 審題結果無法讀取：{exc}")
        return False
    reviewed = attach_reviews(batch, review)
    run.reviewed_path(section_id).write_text(reviewed.model_dump_json(indent=2) + "\n", encoding="utf-8")
    majors = sum(f.severity == "major" for f in review.findings)
    run.update_section(section_id, review_status="reviewed", review_majors=majors)
    log(f"[{section_id}] 審題完成：{len(review.findings)} 筆，其中 major {majors} 筆")
    return True


def revise_section(run: Run, section_id: str, catalog: Catalog, *, worker: str | None = None,
                   log=print) -> bool:
    """Return the non-ok review findings to the writer; the old review no longer matches the content."""
    entry = run.state()["sections"][section_id]
    worker = worker or entry["worker"]
    review = ReviewFile.model_validate_json(run.review_path(section_id).read_text(encoding="utf-8-sig"))
    findings = [f for f in review.findings if f.severity != "ok"]
    if not findings:
        log(f"[{section_id}] 審題沒有需要修改的意見")
        return True
    thread = entry["thread"]
    order = run.root / "workorders" / f"{section_id}-revise.md"
    order.write_text(revision_order(run, section_id, catalog.scope(run.blueprint()), worker=worker,
                                    findings=findings), encoding="utf-8")
    if not agora.is_active(thread):
        log(f"[{section_id}] 修題派給 {worker}（{thread}），共 {len(findings)} 筆意見")
        run.update_section(section_id, review_status="revising")
        agora.assign(thread, worker, order)
    agora.wait(thread)
    report = validate_section(run, section_id, catalog)
    log(f"[{section_id}] 修題後{report.render().splitlines()[0]}")
    if report.errors:
        run.update_section(section_id, review_status="revise_failed")
        return False
    # Keep the review for the record, but stop using its notes: they describe the old content.
    reviewed = run.reviewed_path(section_id)
    if reviewed.exists():
        reviewed.replace(reviewed.with_suffix(".before-revision.json"))
    run.review_path(section_id).replace(run.review_path(section_id).with_suffix(".before-revision.json"))
    run.update_section(section_id, review_status="revised", review_thread=None)
    return True


def attach_reviews(batch: Batch, review: ReviewFile) -> Batch:
    notes = {f.object_id: f for f in review.findings}

    def annotate(obj):
        finding = notes.get(obj.id)
        if finding is None:
            return obj
        return obj.add_review(reviewer=review.reviewer, role="ai", decision="note",
                              notes=f"[{finding.severity}] {finding.notes}")

    groups = []
    for group in batch.groups:
        group = group.model_copy(update={"items": tuple(annotate(i) for i in group.items)})
        groups.append(annotate(group))
    return batch.model_copy(update={"items": tuple(annotate(i) for i in batch.items), "groups": tuple(groups)})


def ai_review_flags(batches: list[Batch]) -> Report:
    """Surface major AI review notes as warnings in the final report."""
    report = Report()
    for batch in batches:
        for obj in (*batch.groups, *batch.all_items()):
            for record in obj.reviews:
                if record.role == "ai" and record.notes.startswith("[major]"):
                    report.add("warnings", "ai_review_major", f"交叉審題指出：{record.notes}", obj=obj)
    return report


def build_run(run: Run, catalog: Catalog, *, force: bool = False, log=print) -> bool:
    blueprint = run.blueprint()
    batches = [b for s in blueprint.sections if (b := run.load_batch(s.id)) is not None]
    report = check_exam(blueprint, batches, lessons=catalog.lessons, wordlist=catalog.wordlist)
    flags = ai_review_flags(batches)
    report.warnings.extend(flags.warnings)
    (run.root / "report.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n",
                                          encoding="utf-8")
    summary = report.render(infos=False)
    (run.root / "report.txt").write_text(summary + "\n", encoding="utf-8")
    log(summary)
    if report.errors and not force:
        log("有錯誤，未輸出 Word（加 --force 可強制輸出）。")
        return False
    out = run.root / "out"
    name = f"{blueprint.header.school_name}{blueprint.exam.academic_year}-{blueprint.exam.title}"
    write_exam(blueprint, batches, out / f"{name}-試題.docx", out / f"{name}-答案解析.docx")
    log(f"已輸出：{(out / f'{name}-試題.docx').as_posix()}、{(out / f'{name}-答案解析.docx').as_posix()}")
    return True
