"""Each reviewer's ratings and comments, saved per run; final ones become human review records."""

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from ..pipeline.runs import Run

DIFFICULTY = {"hard": "困難", "medium": "普通", "easy": "容易"}


def _path(run: Run, reviewer: str):
    if not re.fullmatch(r"[\w㐀-鿿 .-]{1,40}", reviewer):
        raise ValueError("閱卷人名稱只能用中英文、數字、空白、點與連字號")
    return run.root / "feedback" / f"{reviewer}.json"


def load(run: Run, reviewer: str) -> dict:
    path = _path(run, reviewer)
    if not path.exists():
        return {"reviewer": reviewer, "status": "new", "entries": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def save(run: Run, reviewer: str, entries: dict, *, final: bool) -> dict:
    """entries: {object_id: {"content_hash", "difficulty": hard|medium|easy|None, "comment"}}"""
    clean = {}
    for object_id, entry in entries.items():
        difficulty = entry.get("difficulty")
        if difficulty not in (None, *DIFFICULTY):
            raise ValueError(f"{object_id}：難易度只能是 hard、medium、easy")
        comment = str(entry.get("comment") or "").strip()
        if difficulty or comment:
            clean[object_id] = {"content_hash": str(entry.get("content_hash", "")),
                                "difficulty": difficulty, "comment": comment}
    data = {"reviewer": reviewer, "status": "final" if final else "draft",
            "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "entries": clean}
    path = _path(run, reviewer)
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def finals(run: Run) -> list[dict]:
    folder = run.root / "feedback"
    if not folder.exists():
        return []
    data = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]
    return [d for d in data if d.get("status") == "final"]


@dataclass(frozen=True)
class HumanFinding:
    """Same shape as an AI ReviewFinding, so revise_section can send it back to the writer."""
    object_id: str
    severity: str
    notes: str


def findings_for(run: Run, hashes: dict[str, str]) -> list[HumanFinding]:
    """Comments from final reviews on objects that still have the content they were written about.

    hashes maps object id → current content_hash; a comment on an older version is not resent.
    """
    found = []
    for review in finals(run):
        for object_id, entry in review["entries"].items():
            if hashes.get(object_id) == entry["content_hash"] and entry["comment"]:
                level = DIFFICULTY.get(entry.get("difficulty"), "未評")
                found.append(HumanFinding(object_id, f"{review['reviewer']}，難易度{level}", entry["comment"]))
    return found


def attach_human(batch, reviews: list[dict]):
    """Final reviews become human records bound to the content they rated: no comment means approved."""
    def annotate(obj):
        for review in reviews:
            entry = review["entries"].get(obj.id)
            if entry and entry["content_hash"] == obj.content_hash:
                level = DIFFICULTY.get(entry.get("difficulty"), "未評")
                obj = obj.add_review(reviewer=review["reviewer"], role="human",
                                     decision="request_changes" if entry["comment"] else "approve",
                                     notes=f"難易度：{level}" + (f"；{entry['comment']}" if entry["comment"] else ""))
        return obj

    groups = [annotate(g.model_copy(update={"items": tuple(annotate(i) for i in g.items)})) for g in batch.groups]
    return batch.model_copy(update={"items": tuple(annotate(i) for i in batch.items), "groups": tuple(groups)})
