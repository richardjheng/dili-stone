"""The exam as plain JSON for the review page: questions, answers, explanations and drawn figures."""

import base64
import re

from dili.export.figures import render
from dili.models import Table

from ..pipeline.runs import Run


def _figure(figure) -> dict | None:
    if figure is None:
        return None
    if isinstance(figure, Table):
        return {"kind": "table", "title": figure.title,
                "columns": [c.label + (f" ({c.unit})" if c.unit else "") for c in figure.columns],
                "rows": [[row.label, *row.cells] for row in figure.rows]}
    png = base64.b64encode(render(figure)).decode("ascii")
    return {"kind": "image", "title": figure.title, "src": f"data:image/png;base64,{png}"}


def _ai_notes(obj) -> list[str]:
    return [r.notes for r in obj.reviews if r.role == "ai"]


def _item(item) -> dict:
    answer = (f"({item.answer.choice}) {getattr(item.choices, item.answer.choice)}" if item.choices
              else " ／ ".join(item.answer.accepted_text))
    return {"type": "item", "id": item.id, "content_hash": item.content_hash, "number": item.number,
            "kind": item.kind, "stem": item.stem,
            "choices": item.choices.model_dump() if item.choices else None,
            "instruction": item.prompt.instruction if item.prompt else None,
            "answer": answer,
            "explanation": {"correct": item.explanation.correct_zh,
                            "distractors": [{"option": d.option, "reason": d.reason_zh}
                                            for d in item.explanation.distractors]},
            "glossed": [{"word": g.word, "meaning": g.meaning_zh} for g in item.glossed],
            "figure": _figure(item.figure), "ai_notes": _ai_notes(item)}


def _group(group) -> dict:
    numbers = [i.number for i in group.items]
    # Cloze blanks become "[26]" so the page can style them without parsing markup.
    passage = re.sub(r"\{\{(\d+)\}\}", r"[\1]", group.passage or "")
    return {"type": "group", "id": group.id, "content_hash": group.content_hash,
            "first": numbers[0], "last": numbers[-1], "kind": group.kind,
            "instruction": group.instruction, "passage": passage,
            "figure": _figure(group.figure),
            "glossed": [{"word": g.word, "meaning": g.meaning_zh} for g in group.glossed],
            "ai_notes": _ai_notes(group), "items": [_item(i) for i in group.items]}


def exam_payload(run: Run) -> dict:
    blueprint = run.blueprint()
    parts, first = [], 1
    for part in blueprint.parts():
        entries = []
        for section in part.sections:
            batch = run.load_batch(section.id)
            if batch is None:
                raise ValueError(f"{section.id} 尚未出題")
            entries += [_group(g) for g in batch.groups] + [_item(i) for i in batch.items]
        per_item = part.points_per_item
        parts.append({"title": part.title, "first": first, "last": first + part.total_items - 1,
                      "points": f"每題 {per_item.normalize():f} 分，共 {part.total_points.normalize():f} 分"
                      if per_item is not None else f"共 {part.total_points.normalize():f} 分",
                      "entries": entries})
        first += part.total_items
    exam, header = blueprint.exam, blueprint.header
    return {"run_id": run.id, "title": f"{header.school_name} {exam.academic_year} 學年度 {exam.title}",
            "subtitle": f"{header.grade} 年級英文科　範圍：{header.scope_text}", "parts": parts}
