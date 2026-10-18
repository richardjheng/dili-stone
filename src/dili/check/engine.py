"""Exam checks over the existing immutable M0 models."""

from collections import Counter
from difflib import SequenceMatcher
from decimal import Decimal
from itertools import combinations, product
import re

from dili.models import Batch, Blueprint, Lesson, WordList

from .lexicon import LAYER_ORDER, Lemmatizer, Lexicon, expand_headword, load_names, normalize
from .report import Report


def figure_texts(figure):
    """Only visible authored text, never IDs, discriminator values or geometry."""
    if figure is None:
        return
    yield figure.title
    if figure.kind == "street_map":
        yield from figure.vertical_streets
        yield from figure.horizontal_streets
        yield from (place.name for place in figure.places)
    elif figure.kind == "route":
        yield figure.service_day
        for stop in figure.stops:
            yield stop.name
            yield str(stop.arrival_minute)
            yield str(stop.departure_minute)
    elif figure.kind == "table":
        for column in figure.columns:
            yield column.label
            if column.unit:
                yield column.unit
        for row in figure.rows:
            yield row.label
            yield from row.cells
    elif figure.kind == "venn":
        yield figure.left_label
        yield figure.right_label
        yield from dict.fromkeys((*figure.left, *figure.right, *figure.intersection))
    else:
        raise ValueError(f"未知 Figure 類型：{figure.kind}")


def item_texts(item):
    yield item.stem
    if item.choices:
        yield from item.choices.model_dump().values()
    if item.prompt:
        yield item.prompt.instruction
        yield from item.prompt.cues
    yield from item.answer.accepted_text
    yield from figure_texts(item.figure)


def check_text(text, lexicon, report, obj, extended=frozenset()):
    # Cloze placeholders and letter-hint blanks (l______r) are markup, not words.
    text = re.sub(r"\{\{\d+\}\}", " ___ ", text)
    text = re.sub(r"[A-Za-z]*_{2,}[A-Za-z]*", " ___ ", text)
    for match in lexicon.scan(text):
        if match.kind in {"number", "proper_name"}:
            label = "數字" if match.kind == "number" else "可能的專有名詞"
            report.add("infos", match.kind, f"{label}，請人工確認。", obj=obj, words=[match.text])
        elif match.layer == "ambiguous" and any(layer == "outside" for _, layer in match.candidates):
            # Only an out-of-range reading is a risk; scope/prerequisite/gloss mixes are all allowed.
            detail = "、".join(f"{lemma} ({layer})" for lemma, layer in match.candidates)
            report.add("warnings", "ambiguous_lemma", f"詞形跨層歧義，尚未判定通過：{detail}",
                       obj=obj, words=[match.text, *(lemma for lemma, _ in match.candidates)])
        elif match.layer == "outside":
            if any(lemma in extended for lemma, _ in match.candidates):
                message = "課綱 800 字（不在 1200 基本字內），請附註中文或改寫。"
            else:
                message = "超範圍生字，請附註中文或改寫。"
            report.add("errors", "out_of_scope", message, obj=obj, words=[match.text])
        else:
            layer = min((layer for _, layer in match.candidates), key=LAYER_ORDER.get)
            label = {"scope": "本次考點字彙", "prerequisite": "先備字彙", "glossed": "已附註生字"}[layer]
            report.add("infos", match.layer, label, obj=obj, words=[match.text])


def _options(item):
    if item.choices is None:
        return None
    return tuple(sorted(normalize(s) for s in item.choices.model_dump().values()))


def _similarities(pairs, report, threshold, *, cross=False):
    for first, second in pairs:
        a, b = normalize(first.stem), normalize(second.stem)
        # Chinese-only stems still participate in duplicate detection.
        a = a or re.sub(r"\W+", "", first.stem.casefold())
        b = b or re.sub(r"\W+", "", second.stem.casefold())
        prefix = "cross_exam_" if cross else ""
        if a and b and SequenceMatcher(None, a, b, autojunk=False).ratio() >= threshold:
            report.add("warnings", prefix + "similar_stem",
                       f"題幹與第 {first.number} 題 ({first.id}) 相同或高度相似。",
                       obj=second, words=[first.stem, second.stem])
        choices = _options(first)
        if choices is not None and choices == _options(second):
            report.add("warnings", prefix + "duplicate_choices",
                       f"選項組合與第 {first.number} 題 ({first.id}) 完全相同（不計順序）。",
                       obj=second, words=choices)


def compare_exams(first, second, *, similarity_threshold=0.9):
    """Compare two iterables of Batch; returns only cross-exam duplicate warnings.

    Pending and scope checks belong to check_exam, which needs a blueprint.
    IDs may repeat between exams; identity is never used to suppress a pair.
    """
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold 必須介於 0 和 1")
    report = Report()
    left = [i for b in first for i in b.all_items()]
    right = [i for b in second for i in b.all_items()]
    _similarities(product(left, right), report, similarity_threshold, cross=True)
    return report


def check_exam(blueprint: Blueprint, batches, *, lessons, wordlist: WordList,
               similarity_threshold=0.9, max_target_uses=3, irregular_path=None):
    """Check one exam without mutating any input. Thresholds are caller-configurable."""
    if not 0 <= similarity_threshold <= 1:
        raise ValueError("similarity_threshold 必須介於 0 和 1")
    if isinstance(max_target_uses, bool) or not isinstance(max_target_uses, int) or max_target_uses < 1:
        raise ValueError("max_target_uses 必須是正整數")
    batches, lessons = tuple(batches), tuple(lessons)
    report = Report()
    catalog = {lesson.id: lesson for lesson in lessons}
    if len(catalog) != len(lessons):
        report.add("errors", "duplicate_lesson", "教材目錄包含重複 lesson ID。")
    for lesson_id in (*blueprint.scope, *blueprint.prerequisite.lesson_ids):
        if lesson_id not in catalog:
            report.add("errors", "missing_lesson", "缺少範圍或先備教材。", words=[lesson_id])
    if wordlist.id != blueprint.prerequisite.wordlist_id:
        report.add("errors", "wordlist_mismatch", "先備字表 ID 與藍圖不符。",
                   words=[blueprint.prerequisite.wordlist_id, wordlist.id])

    sections = {s.id: s for s in blueprint.sections}
    counts = Counter(b.section_id for b in batches)
    for section_id in sections:
        if counts[section_id] != 1:
            report.add("errors", "section_batch_count", f"大題 {section_id} 必須恰有一份 batch，實際 {counts[section_id]} 份。")
    for batch in batches:
        try:
            batch.validate_against(blueprint, lessons=lessons)
        except ValueError as exc:
            report.add("errors", "batch_structure", f"大題 {batch.section_id} 結構或考點引用錯誤：{exc}")
    items = sorted((i for batch in batches for i in batch.all_items()), key=lambda i: i.number)
    groups = [g for b in batches for g in b.groups]
    ids = Counter(obj.id for obj in (*groups, *items))
    for obj in (*groups, *items):
        if ids[obj.id] > 1:
            report.add("errors", "duplicate_id", "整卷題目／題組 ID 重複。", obj=obj)
    if [i.number for i in items] != list(range(1, blueprint.total_items + 1)):
        report.add("errors", "numbering", f"整卷題號必須為 1..{blueprint.total_items}，不得重複或缺漏。")
    points = sum((sections[i.section_id].points_per_item for i in items if i.section_id in sections), Decimal(0))
    if points != blueprint.total_points:
        report.add("errors", "total_points", f"總分應為 {blueprint.total_points}，實際可計算配分為 {points}。")

    scope = [v for key in blueprint.scope if key in catalog for v in catalog[key].vocab]
    prerequisite = [v.headword for key in blueprint.prerequisite.lesson_ids if key in catalog for v in catalog[key].vocab]
    extended = set()
    if wordlist.id == blueprint.prerequisite.wordlist_id:
        # Only the 1,200 basic words may appear freely; the other 800 need a gloss (Richard, 2026-10-18).
        prerequisite.extend(e.headword for e in wordlist.entries if e.basic_1200)
        for e in wordlist.entries:
            if not e.basic_1200:
                extended.update(expand_headword(e.headword)[1])
    lemmatizer = Lemmatizer(irregular_path)
    names = load_names()

    def lexicon(glosses):
        return Lexicon([v.headword for v in scope], prerequisite, [g.word for g in glosses],
                       application=[v.headword for v in scope if v.textbook_category == "application"],
                       lemmatizer=lemmatizer, names=names)

    group_for_item = {id(item): group for group in groups for item in group.items}
    pending_items = sum(i.status == "pending" for i in items)
    pending_groups = sum(g.status == "pending" for g in groups)
    report.pending_count = pending_items + pending_groups
    report.add("infos", "pending_summary", f"待審題目 {pending_items} 題、題組 {pending_groups} 組；未變更審核狀態。")
    for group in groups:
        group_lexicon = lexicon(group.glossed)
        for text in (group.instruction, group.passage or "", *figure_texts(group.figure)):
            check_text(text, group_lexicon, report, group, extended)
        if group.status == "pending":
            report.add("infos", "pending", "題組待人工審核。", obj=group)
    for item in items:
        group = group_for_item.get(id(item))
        item_lexicon = lexicon((*group.glossed, *item.glossed) if group else item.glossed)
        for text in item_texts(item):
            check_text(text, item_lexicon, report, item, extended)
        if item.choices and item.targets.vocab_ids and not item.targets.grammar_ids:
            for letter, text in item.choices.model_dump().items():
                if not item_lexicon.is_target(text):
                    report.add("errors", "option_not_scope", f"單字考點選擇題的 {letter} 選項必須完整對應範圍內字彙／片語。",
                               obj=item, words=[text])
        if item.kind == "word_in_context":
            for answer in item.answer.accepted_text:
                if not item_lexicon.is_target(answer, application=True):
                    report.add("errors", "answer_not_application", "文意字彙答案必須完整對應範圍內 application 字彙。",
                               obj=item, words=[answer])
                letters = re.sub(r"[^a-z]", "", answer.lower())
                for name, actual in (("first_letter", letters[:1]), ("last_letter", letters[-1:])):
                    hint = getattr(item.prompt, name)
                    if hint and hint.lower() != actual:
                        report.add("errors", "letter_hint_mismatch", f"{name} 提示 {hint} 與答案不一致。", obj=item, words=[answer])
        if item.status == "pending":
            report.add("infos", "pending", "題目待人工審核。", obj=item)
    # A cloze sub-item's stem is boilerplate ("blank 26"); its content is the passage.
    _similarities(combinations([i for i in items if i.kind != "cloze"], 2), report, similarity_threshold)
    target_items = {}
    for item in items:
        for target in (*item.targets.vocab_ids, *item.targets.grammar_ids):
            target_items.setdefault(target, []).append(item)
    for target, uses in target_items.items():
        if len(uses) > max_target_uses:
            numbers = "、".join(str(i.number) for i in uses)
            report.add("warnings", "target_overuse", f"同一考點出現 {len(uses)} 次（上限 {max_target_uses}）：第 {numbers} 題。",
                       obj=uses[-1], words=[target])
    return report
