"""Spread the scope's targets over the sections so parallel writers do not collide."""

import random
import re

from dili.models import Blueprint, Lesson

# Who writes and who cross-reviews each section kind. Reviewers always differ from writers.
WORKERS = {"single_choice": "antigravity", "cloze": "claude", "reading": "antigravity",
           "word_in_context": "codex", "guided_writing": "claude"}
REVIEWERS = {"single_choice": "claude", "cloze": "antigravity", "reading": "claude",
             "word_in_context": "antigravity", "guided_writing": "antigravity"}


def _round_robin(groups: list[list[str]], count: int, rng: random.Random) -> list[str]:
    """Take ids alternately from each lesson so every unit is represented."""
    pools = [rng.sample(g, len(g)) for g in groups if g]
    picked: list[str] = []
    while len(picked) < count and any(pools):
        for pool in pools:
            if pool and len(picked) < count:
                picked.append(pool.pop())
    return picked


def _single_word(headword: str) -> bool:
    return re.fullmatch(r"[a-z]+", headword) is not None


def allocate(blueprint: Blueprint, lessons: list[Lesson], seed: str) -> dict:
    """Required targets per section; anything not listed stays free for the writer to use."""
    rng = random.Random(seed)
    used: set[str] = set()

    def vocab(predicate, count):
        groups = [[v.id for v in lesson.vocab if predicate(v) and v.id not in used] for lesson in lessons]
        picked = _round_robin(groups, count, rng)
        used.update(picked)
        return picked

    grammar = [[g.id for g in lesson.grammar] for lesson in lessons]
    all_grammar = [g for group in grammar for g in group]
    plan = {}
    for section in blueprint.sections:
        required = {"vocab": [], "grammar": []}
        if section.kind == "word_in_context":
            required["vocab"] = vocab(lambda v: v.textbook_category == "application"
                                      and not v.is_phrase and _single_word(v.headword), section.item_count)
        elif section.kind == "single_choice":
            required["vocab"] = vocab(lambda v: v.textbook_category == "application", section.item_count * 2 // 5)
            required["grammar"] = list(all_grammar)
        elif section.kind == "guided_writing":
            required["grammar"] = _round_robin(grammar, section.item_count, rng)
        plan[section.id] = {"kind": section.kind, "worker": WORKERS[section.kind],
                            "reviewer": REVIEWERS[section.kind], "required": required}
    return plan
