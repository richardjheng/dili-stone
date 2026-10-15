"""Command-line exam checking. Also callable via python -m dili.cli."""

import argparse
import json
from pathlib import Path

from dili.check import Report, check_exam
from dili.models import Batch, Blueprint, Lesson, load_wordlist


def main(argv=None):
    parser = argparse.ArgumentParser(prog="dili", description="砥礪英文考卷檢核")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="檢查題目範圍、結構與重複")
    check.add_argument("blueprint", type=Path)
    check.add_argument("batch_dir", type=Path)
    check.add_argument("--lessons", type=Path, default=Path("data/lessons"))
    check.add_argument("--wordlist", type=Path, default=Path("data/wordlists/moe2000.csv"))
    check.add_argument("--json", dest="json_report", type=Path)
    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
