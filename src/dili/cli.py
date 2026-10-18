"""Command-line exam checking. Also callable via python -m dili.cli."""

import argparse
import json
from pathlib import Path

from dili.check import Report, check_exam
from dili.models import Batch, Blueprint, Lesson, load_wordlist


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dili", description="砥礪英文考卷")
    commands = parser.add_subparsers(dest="command", required=True)
    plan = commands.add_parser("plan", help="建立一次出卷：分配考點並產生工作單")
    plan.add_argument("blueprint", type=Path)
    plan.add_argument("--run", required=True, help="出卷 ID，例如 b3-term2-a")
    for name, help_text in (("generate", "經 Agora 派工出題，交付後自動檢核，有錯就帶著錯誤重派"),
                            ("review", "請另一個 AI 交叉審題，意見附加為 AI 審核紀錄"),
                            ("revise", "把審題意見交回出題者修改，改完重新檢核")):
        step = commands.add_parser(name, help=help_text)
        step.add_argument("run")
        step.add_argument("--section", action="append", help="只處理指定大題（可重複）")
        step.add_argument("--to", dest="worker", help="改派給 claude、codex 或 antigravity")
    review_ui = commands.add_parser("review-ui", help="開啟本機閱卷網頁：每題評難易度、寫意見，可暫存與完成閱卷")
    review_ui.add_argument("run", nargs="?", help="預設為 runs/CURRENT（最近一次出卷）")
    review_ui.add_argument("--reviewer", default="Richard", help="閱卷人名稱（每人分開存）")
    review_ui.add_argument("--port", type=int, default=8765)
    review_ui.add_argument("--no-browser", action="store_true")
    feedback = commands.add_parser("feedback", help="把已完成的閱卷意見交回出題者修題")
    feedback.add_argument("run")
    feedback.add_argument("--section", action="append")
    feedback.add_argument("--to", dest="worker", help="改派給 claude、codex 或 antigravity 修題")
    build = commands.add_parser("build", help="整卷檢核並輸出試題卷與答案解析卷")
    build.add_argument("run")
    build.add_argument("--force", action="store_true", help="有錯誤也輸出 Word")
    validate = commands.add_parser("validate", help="檢查已交付的大題（agora build 會呼叫）")
    validate.add_argument("run", nargs="?", help="預設為 runs/CURRENT")
    validate.add_argument("--section", action="append")
    status = commands.add_parser("status", help="顯示出卷進度")
    status.add_argument("run", nargs="?")
    check = commands.add_parser("check", help="檢查題目範圍、結構與重複")
    check.add_argument("blueprint", type=Path)
    check.add_argument("batch_dir", type=Path)
    check.add_argument("--lessons", type=Path, default=Path("data/lessons"))
    check.add_argument("--wordlist", type=Path, default=Path("data/wordlists/moe2000.csv"))
    check.add_argument("--json", dest="json_report", type=Path)
    args = parser.parse_args(argv)
    if args.command != "check":
        return _pipeline(args)
    report = Report()
    try:
        if not args.batch_dir.is_dir() or not args.lessons.is_dir():
            raise ValueError("batch_dir 與 lessons 必須是存在的目錄")
        blueprint = Blueprint.model_validate_json(args.blueprint.read_text(encoding="utf-8-sig"))
        batches = [Batch.model_validate_json(path.read_text(encoding="utf-8-sig"))
                   for path in sorted(args.batch_dir.glob("*.json"))]
        lessons = [Lesson.model_validate_json(path.read_text(encoding="utf-8-sig"))
                   for path in sorted(args.lessons.rglob("*.json"))]
        wordlist = load_wordlist(args.wordlist)
        report = check_exam(blueprint, batches, lessons=lessons, wordlist=wordlist)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        report.add("errors", "input_error", f"無法載入或檢核資料：{exc}")
    if args.json_report:
        try:
            # Do not let --json overwrite any input, including a lesson or batch.
            output = args.json_report.resolve()
            protected = {args.blueprint.resolve(), args.wordlist.resolve(), args.wordlist.with_suffix(".meta.yaml").resolve()}
            if (output in protected or output.is_relative_to(args.lessons.resolve())
                    or output.is_relative_to(args.batch_dir.resolve())):
                raise ValueError("JSON 報告不可覆寫輸入檔案或放在教材／batch 目錄中")
            args.json_report.write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except (OSError, ValueError) as exc:
            report.add("errors", "report_write_error", f"無法寫入 JSON 報告：{exc}")
    print(report.render())
    return 1 if report.errors else 0


def _pipeline(args) -> int:
    from dili import pipeline

    catalog = pipeline.Catalog.load()
    if args.command == "plan":
        run = pipeline.plan_run(args.run, args.blueprint, catalog)
        print(f"已建立 {run.root.as_posix()}；分工：")
        for section_id, entry in run.plan().items():
            print(f"  {section_id}: 出題 {entry['worker']}，審題 {entry['reviewer']}")
        return 0
    run = pipeline.Run.open(args.run)
    sections = getattr(args, "section", None) or [s.id for s in run.blueprint().sections]
    if args.command == "generate":
        results = [pipeline.generate_section(run, s, catalog, worker=args.worker) for s in sections]
        return 0 if all(results) else 1
    if args.command == "review":
        results = [pipeline.review_section(run, s, catalog, reviewer=args.worker) for s in sections]
        return 0 if all(results) else 1
    if args.command == "revise":
        results = [pipeline.revise_section(run, s, catalog, worker=args.worker) for s in sections]
        return 0 if all(results) else 1
    if args.command == "review-ui":
        from dili.review import serve
        serve(run, reviewer=args.reviewer, port=args.port, open_browser=not args.no_browser)
        return 0
    if args.command == "feedback":
        from dili.review import findings_for
        results = []
        for section_id in sections:
            batch = run.load_batch(section_id)
            hashes = {x.id: x.content_hash for x in (*batch.groups, *batch.all_items())} if batch else {}
            found = findings_for(run, hashes)
            # Checker errors on the same objects (e.g. 800-list words without a gloss) go along too.
            from dili.review.store import HumanFinding
            report = pipeline.validate_section(run, section_id, catalog)
            found += [HumanFinding(e.item_id, "程式檢核", f"{e.message}（{'、'.join(dict.fromkeys(e.words))}）")
                      for e in report.errors if e.item_id]
            print(f"[{section_id}] 閱卷意見與檢核錯誤 {len(found)} 筆")
            if found:
                results.append(pipeline.revise_section(run, section_id, catalog, findings=found,
                                                       worker=args.worker))
        return 0 if all(results) else 1
    if args.command == "build":
        return 0 if pipeline.build_run(run, catalog, force=args.force) else 1
    if args.command == "validate":
        delivered = [s for s in sections if run.batch_path(s).exists()]
        if not delivered:
            print(f"{run.id}：還沒有任何已交付的大題。")
            return 1
        failed = False
        for section_id in delivered:
            report = pipeline.validate_section(run, section_id, catalog)
            print(f"== {section_id}（{run.batch_path(section_id).as_posix()}）")
            print(report.render(infos=False))
            failed |= bool(report.errors)
        return 1 if failed else 0
    for section_id, entry in run.state()["sections"].items():
        print(f"{section_id}: " + "，".join(f"{k}={v}" for k, v in entry.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
