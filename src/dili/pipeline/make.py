"""One command for a whole new exam: plan → generate → cross-review → revise → check.

Sections handled by the same AI run one after another; different AIs work in parallel.
A section whose writer fails is handed to the next AI in FALLBACK; a reviewer is never the writer.
"""

import threading
import time
from collections import defaultdict
from pathlib import Path

from .runs import Catalog, Run
from .steps import build_run, generate_section, plan_run, review_section, revise_section

FALLBACK = ("antigravity", "codex", "claude")
STAGGER = 10  # seconds between parallel Agora starts (see agora.wait for the start-up race)


def _parallel(groups: dict[str, list[str]], work, log) -> dict[str, bool]:
    """Run work(section, ai) per AI in its own thread; returns {section: ok}."""
    results: dict[str, bool] = {}

    def lane(ai, sections):
        for section in sections:
            try:
                results[section] = work(section, ai)
            except Exception as exc:  # keep the other lanes going
                log(f"[{section}] {ai} 發生錯誤：{exc}")
                results[section] = False

    threads = []
    for i, (ai, sections) in enumerate(groups.items()):
        thread = threading.Thread(target=lane, args=(ai, sections), daemon=True)
        if i:
            time.sleep(STAGGER)
        thread.start()
        threads.append(thread)
    for thread in threads:
        thread.join()
    return results


def _by(run: Run, key: str, sections: list[str]) -> dict[str, list[str]]:
    state = run.state()["sections"]
    groups = defaultdict(list)
    for section in sections:
        groups[state[section][key]].append(section)
    return dict(groups)


def make_exam(run_id: str, blueprint: Path, catalog: Catalog, *, log=print) -> Run:
    run = plan_run(run_id, blueprint, catalog)
    sections = [s.id for s in run.blueprint().sections]
    log(f"已建立 {run.root.as_posix()}，共 {len(sections)} 個大題。")

    log("== 1/4 出題")
    pending = list(sections)
    for attempt in range(len(FALLBACK)):
        results = _parallel(_by(run, "worker", pending),
                            lambda s, ai: generate_section(run, s, catalog, worker=ai, log=log), log)
        pending = [s for s, ok in results.items() if not ok]
        if not pending:
            break
        for section in pending:
            failed = run.state()["sections"][section]["worker"]
            nxt = FALLBACK[(FALLBACK.index(failed) + 1) % len(FALLBACK)] if failed in FALLBACK else FALLBACK[0]
            log(f"[{section}] {failed} 沒有完成，改派給 {nxt}")
            run.update_section(section, worker=nxt, thread=None)
    if pending:
        raise RuntimeError(f"這些大題出題失敗：{', '.join(pending)}（詳見 runs/{run.id}/state）")

    log("== 2/4 交叉審題")
    for section in sections:  # the reviewer must differ from whoever finally wrote it
        entry = run.state()["sections"][section]
        if entry["reviewer"] == entry["worker"]:
            run.update_section(section, reviewer=next(a for a in FALLBACK if a != entry["worker"]))
    _parallel(_by(run, "reviewer", sections),
              lambda s, ai: review_section(run, s, catalog, reviewer=ai, log=log), log)

    log("== 3/4 依審題意見修題")
    reviewed = [s for s in sections if run.state()["sections"][s].get("review_status") == "reviewed"]
    _parallel(_by(run, "worker", reviewed),
              lambda s, ai: revise_section(run, s, catalog, worker=ai, log=log), log)

    log("== 4/4 整卷檢核")
    build_run(run, catalog, log=log, write_word=False)
    run.make_current()
    return run
