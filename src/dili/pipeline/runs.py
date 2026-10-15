"""A run is one exam being produced: its blueprint copy, work orders, batches, reviews and output."""

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from dili.models import Batch, Blueprint, Lesson, WordList, load_wordlist

RUNS = Path("runs")
CURRENT = RUNS / "CURRENT"
LESSONS = Path("data/lessons")
WORDLIST = Path("data/wordlists/moe2000.csv")


@dataclass(frozen=True)
class Catalog:
    lessons: tuple[Lesson, ...]
    wordlist: WordList

    @classmethod
    def load(cls, lessons_dir: Path = LESSONS, wordlist: Path = WORDLIST) -> "Catalog":
        lessons = tuple(Lesson.model_validate_json(p.read_text(encoding="utf-8-sig"))
                        for p in sorted(lessons_dir.rglob("*.json")))
        return cls(lessons, load_wordlist(wordlist))

    def scope(self, blueprint: Blueprint) -> list[Lesson]:
        by_id = {lesson.id: lesson for lesson in self.lessons}
        missing = [i for i in blueprint.scope if i not in by_id]
        if missing:
            raise ValueError(f"缺少範圍教材：{', '.join(missing)}")
        return [by_id[i] for i in blueprint.scope]


class Run:
    def __init__(self, root: Path):
        self.root = root
        self.id = root.name

    @classmethod
    def create(cls, run_id: str, blueprint_path: Path, base: Path = RUNS) -> "Run":
        root = base / run_id
        if root.exists():
            raise ValueError(f"已有同名的出卷：{root}")
        Blueprint.model_validate_json(blueprint_path.read_text(encoding="utf-8-sig"))
        for name in ("state", "workorders", "batches", "reviews", "reviewed", "out"):
            (root / name).mkdir(parents=True)
        shutil.copyfile(blueprint_path, root / "blueprint.json")
        return cls(root)

    @classmethod
    def open(cls, run_id: str | None = None, base: Path = RUNS) -> "Run":
        if run_id is None:
            if not CURRENT.exists():
                raise ValueError("沒有指定出卷，也沒有 runs/CURRENT")
            run_id = CURRENT.read_text(encoding="utf-8").strip()
        root = base / run_id
        if not (root / "blueprint.json").exists():
            raise ValueError(f"找不到出卷：{root}")
        return cls(root)

    def make_current(self) -> None:
        CURRENT.write_text(self.id + "\n", encoding="utf-8")

    def blueprint(self) -> Blueprint:
        return Blueprint.model_validate_json((self.root / "blueprint.json").read_text(encoding="utf-8-sig"))

    def state(self) -> dict:
        """Each section keeps its own file, so parallel generate processes never overwrite each other."""
        folder = self.root / "state"
        return {"sections": {p.stem: json.loads(p.read_text(encoding="utf-8"))
                             for p in sorted(folder.glob("*.json"))}}

    def update_section(self, section_id: str, **fields) -> None:
        path = self.root / "state" / f"{section_id}.json"
        entry = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        entry.update(fields)
        path.write_text(json.dumps(entry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def plan(self) -> dict:
        return json.loads((self.root / "plan.json").read_text(encoding="utf-8"))

    def batch_path(self, section_id: str) -> Path:
        return self.root / "batches" / f"{section_id}.json"

    def reviewed_path(self, section_id: str) -> Path:
        return self.root / "reviewed" / f"{section_id}.json"

    def review_path(self, section_id: str) -> Path:
        return self.root / "reviews" / f"{section_id}.json"

    def load_batch(self, section_id: str, *, reviewed: bool = True) -> Batch | None:
        for path in ((self.reviewed_path(section_id),) if reviewed else ()) + (self.batch_path(section_id),):
            if path.exists():
                return Batch.model_validate_json(path.read_text(encoding="utf-8-sig"))
        return None
