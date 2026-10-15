"""Handwritten fixtures only. unittest and pytest both collect these tests."""

import copy
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError

from dili.models import (
    Batch, Blueprint, Group, Item, Lesson, Prerequisite, ReviewRecord, Section,
    WordList, figure_adapter, load_wordlist,
)
from dili.models.export_schema import export_schemas


def fixture(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def item(**updates):
    data = fixture("synthetic_item.json")
    data.update(updates)
    return Item.model_validate(data)


def approve(value):
    return value.add_review(reviewer="fixture-human", role="human", decision="approve", notes="測試人工紀錄")


def small_blueprint(**section_updates):
    data = fixture("term_blueprint.json")
    section = dict(id="choice", title="測試", kind="single_choice", item_count=1, points_per_item="2")
    section.update(section_updates)
    data.update(scope=["demo-b3-u4"], sections=[section], total_items=section["item_count"],
                total_points=str(Decimal(section["points_per_item"]) * section["item_count"]))
    return Blueprint.model_validate(data)


class BlueprintTests(unittest.TestCase):
    def test_term_example_has_60_questions_100_points(self):
        bp = Blueprint.model_validate(fixture("term_blueprint.json"))
        self.assertEqual(bp.total_items, 60)
        self.assertEqual(bp.total_points, Decimal(100))
        self.assertEqual(sum(s.item_count for s in bp.sections if s.kind in {"cloze", "reading"}), 20)
        self.assertEqual(bp.prerequisite.lesson_ids, ())
        self.assertEqual(bp.status, "pending")

    def test_bad_total_points(self):
        with self.assertRaisesRegex(ValidationError, "total_points"):
            small_blueprint().model_copy(update={"total_points": "99"})

    def test_bad_total_items(self):
        with self.assertRaisesRegex(ValidationError, "total_items"):
            small_blueprint().model_copy(update={"total_items": 2})

    def test_group_counts_and_sizes(self):
        base = dict(id="c", title="克漏字", kind="cloze", item_count=6, points_per_item="1.5",
                    group_count=2, items_per_group=[3, 3])
        self.assertEqual(Section(**base).group_count, 2)
        for updates in ({"group_count": 1}, {"items_per_group": [2, 3]},
                        {"group_count": 0, "items_per_group": []}, {"items_per_group": [0, 6]}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                Section(**(base | updates))

    def test_standalone_section_cannot_have_groups(self):
        with self.assertRaises(ValidationError):
            small_blueprint(group_count=1, items_per_group=[1])

    def test_prerequisite_is_explicit(self):
        with self.assertRaises(ValidationError):
            Prerequisite(wordlist_id="moe2000")
        with self.assertRaises(ValidationError):
            Prerequisite(lesson_ids=[])

    def test_duplicate_sections_scope_and_overlap(self):
        bp = small_blueprint()
        for updates in ({"scope": ["demo-b3-u4", "demo-b3-u4"]},
                        {"prerequisite": {"wordlist_id": "moe2000", "lesson_ids": ["demo-b3-u4"]}},
                        {"sections": [bp.sections[0], bp.sections[0]], "total_items": 2, "total_points": "4"}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                bp.model_copy(update=updates)

    def test_positive_finite_scores_and_integer_counts(self):
        for key, value in (("points_per_item", "NaN"), ("points_per_item", "Infinity"),
                           ("points_per_item", "0"), ("item_count", True), ("item_count", 1.5)):
            with self.subTest(key=key, value=value), self.assertRaises((ValidationError, ValueError)):
                small_blueprint().sections[0].model_copy(update={key: value})


class ReviewTests(unittest.TestCase):
    def test_new_content_is_pending(self):
        self.assertEqual(item().status, "pending")

    def test_human_can_approve_current_content(self):
        reviewed = approve(item())
        self.assertEqual(reviewed.status, "reviewed")
        self.assertEqual(reviewed.reviews[-1].content_hash, reviewed.content_hash)
        self.assertEqual(Item.model_validate_json(reviewed.model_dump_json()).status, "reviewed")

    def test_ai_only_adds_notes(self):
        pending = item().add_review(reviewer="test-ai", role="ai", notes="建議檢查")
        self.assertEqual(pending.status, "pending")
        for decision in ("approve", "request_changes"):
            with self.subTest(decision=decision), self.assertRaisesRegex(ValidationError, "AI review"):
                item().add_review(reviewer="test-ai", role="ai", decision=decision, notes="不允許")

    def test_ai_note_does_not_revoke_human_approval(self):
        reviewed = approve(item())
        noted = reviewed.add_review(reviewer="test-ai", role="ai", notes="保留建議")
        self.assertEqual(reviewed.content_hash, noted.content_hash)
        self.assertEqual(noted.status, "reviewed")

    def test_content_changes_return_pending(self):
        reviewed = approve(item())
        for updates in ({"stem": "A different handwritten question."},
                        {"glossed": [{"word": "crate", "meaning_zh": "箱子"}]},
                        {"targets": {"grammar_ids": ["different-target"]}},
                        {"explanation": {"correct_zh": "另一段解析", "distractors": reviewed.explanation.distractors}}):
            with self.subTest(updates=updates):
                changed = reviewed.model_copy(update=updates)
                self.assertEqual(changed.status, "pending")
                self.assertNotEqual(changed.content_hash, reviewed.content_hash)
                self.assertEqual(len(changed.reviews), 1)

    def test_nested_edit_on_serialized_content_invalidates_approval(self):
        data = approve(item()).model_dump(mode="json")
        data["choices"]["D"] = "to paint"
        self.assertEqual(Item.model_validate(data).status, "pending")

    def test_forged_status_and_old_hash_are_ignored(self):
        data = item().model_dump(mode="json")
        data.update(status="reviewed", content_hash="0" * 64)
        restored = Item.model_validate(data)
        self.assertEqual(restored.status, "pending")
        self.assertNotEqual(restored.content_hash, "0" * 64)

    def test_record_for_other_content_cannot_approve(self):
        reviewed = approve(item())
        record = reviewed.reviews[0].model_copy(update={"content_hash": "0" * 64})
        self.assertEqual(reviewed.model_copy(update={"reviews": [record]}).status, "pending")

    def test_unresolved_issues_cannot_be_approved(self):
        for kind in ("sense", "prerequisite", "grammar"):
            with self.subTest(kind=kind):
                pending = item().model_copy(update={"unresolved": [{"kind": kind, "reference": "test", "notes": "待確認"}]})
                self.assertEqual(approve(pending).status, "pending")

    def test_later_human_request_changes_revokes_approval(self):
        reviewed = approve(item())
        revised = reviewed.add_review(reviewer="fixture-human", role="human", decision="request_changes", notes="請重寫")
        self.assertEqual(revised.status, "pending")

    def test_immutable_values_and_validated_copy(self):
        reviewed = approve(item())
        with self.assertRaises(ValidationError):
            reviewed.stem = "changed"
        with self.assertRaises(ValidationError):
            reviewed.choices.A = "changed"
        with self.assertRaises(ValidationError):
            reviewed.model_copy(update={"number": 0})

    def test_review_requires_timezone_and_valid_hash(self):
        base = dict(reviewer="person", role="human", timestamp=datetime.now(timezone.utc),
                    content_hash=item().content_hash, notes="紀錄")
        for update in ({"timestamp": datetime(2026, 1, 1)}, {"content_hash": "wrong"}):
            with self.subTest(update=update), self.assertRaises(ValidationError):
                ReviewRecord(**(base | update))

    def test_parent_cannot_hide_pending_children(self):
        group = Group.model_validate(fixture("synthetic_group.json"))
        self.assertEqual(approve(group).status, "pending")
        group = group.model_copy(update={"items": [approve(group.items[0])]})
        group = approve(group)
        self.assertEqual(group.status, "reviewed")
        self.assertEqual(group.model_copy(update={"passage": "Mira enjoys {{26}} wooden toys."}).status, "pending")
        child = group.items[0].add_review(reviewer="human", role="human", decision="request_changes", notes="請查證")
        self.assertEqual(group.model_copy(update={"items": [child]}).status, "pending")


class LessonTests(unittest.TestCase):
    def test_category_and_second_sense_are_independent(self):
        lesson = Lesson.model_validate(fixture("synthetic_lesson.json"))
        vocab = lesson.vocab[0]
        self.assertEqual(vocab.textbook_category, "application")
        self.assertEqual([s.is_secondary for s in vocab.senses], [False, True])
        changed = vocab.model_copy(update={"textbook_category": "recognition"})
        self.assertEqual([s.is_secondary for s in changed.senses], [False, True])
        self.assertEqual(vocab.pos, ("v.", "n."))

    def test_unknown_source_reference_rejected(self):
        data = fixture("synthetic_lesson.json")
        data["vocab"][0]["source_refs"] = ["unknown"]
        with self.assertRaisesRegex(ValidationError, "source reference"):
            Lesson.model_validate(data)

    def test_duplicate_target_ids_rejected(self):
        data = fixture("synthetic_lesson.json")
        data["grammar"][0]["id"] = data["vocab"][0]["id"]
        with self.assertRaises(ValidationError):
            Lesson.model_validate(data)

    def test_source_dual_pages_and_partial_coverage(self):
        data = fixture("synthetic_lesson.json")
        data["sources"][0].update(pdf_page=2, printed_page="U4-1")
        lesson = Lesson.model_validate(data)
        self.assertEqual(lesson.sources[0].printed_page, "U4-1")
        data["sources"][0]["pdf_page"] = 0
        with self.assertRaises(ValidationError):
            Lesson.model_validate(data)


class ItemGroupTests(unittest.TestCase):
    def test_all_unselected_options_require_explanations(self):
        data = fixture("synthetic_item.json")
        for distractors in (data["explanation"]["distractors"][:2],
                            [data["explanation"]["distractors"][0]] * 3):
            bad = copy.deepcopy(data)
            bad["explanation"]["distractors"] = distractors
            with self.assertRaises(ValidationError):
                Item.model_validate(bad)

    def test_chinese_explanation_required(self):
        data = fixture("synthetic_item.json")
        data["explanation"]["correct_zh"] = "English only is insufficient."
        with self.assertRaises(ValidationError):
            Item.model_validate(data)

    def test_answer_and_options_are_consistent(self):
        for updates in ({"choices": None}, {"answer": {"choice": "E"}},
                        {"answer": {"choice": "A", "accepted_text": ["painting"]}},
                        {"choices": {"A": "paint", "B": "paint", "C": "painted", "D": "paints"}},
                        {"targets": {}}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                item(**updates)

    def test_written_question_types(self):
        for kind, task in (("word_in_context", "word_in_context"), ("guided_writing", "rewrite"),
                           ("guided_writing", "combine"), ("guided_writing", "translate_zh_en")):
            with self.subTest(task=task):
                question = item(kind=kind, choices=None,
                                prompt={"task": task, "instruction": "請依提示填答。", "first_letter": "p"},
                                answer={"accepted_text": ["painting"]}, explanation={"correct_zh": "依動名詞規則作答。"})
                self.assertEqual(question.answer.accepted_text, ("painting",))

    def test_written_task_mismatch(self):
        with self.assertRaises(ValidationError):
            item(kind="guided_writing", choices=None,
                 prompt={"task": "word_in_context", "instruction": "測試"},
                 answer={"accepted_text": ["paint"]}, explanation={"correct_zh": "測試"})

    def test_cloze_placeholder_matches_number(self):
        group = Group.model_validate(fixture("synthetic_group.json"))
        self.assertEqual(group.items[0].number, 26)
        for passage in ("No blank.", "Wrong {{27}}.", "Repeated {{26}} and {{26}}.",
                        "Padded {{026}}.", "{{26}} and malformed {{x}}."):
            with self.subTest(passage=passage), self.assertRaises(ValidationError):
                group.model_copy(update={"passage": passage})

    def test_group_order_ids_and_section(self):
        group = Group.model_validate(fixture("synthetic_group.json"))
        child = group.items[0]
        for updates in ({"items": [child, child]},
                        {"items": [child.model_copy(update={"section_id": "other"})]},
                        {"items": [child, child.model_copy(update={"id": "q28", "number": 28})],
                         "passage": "{{26}} and {{28}}."}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                group.model_copy(update=updates)

    def test_reading_figure_only(self):
        group = Group(id="read", section_id="reading", kind="reading", instruction="Read the table.",
                      figure=fixture("figures.json")[2], items=[item(kind="reading", section_id="reading")])
        self.assertIsNone(group.passage)
        with self.assertRaises(ValidationError):
            group.model_copy(update={"figure": None})


class FigureTests(unittest.TestCase):
    def test_union_roundtrips_all_kinds(self):
        for data in fixture("figures.json"):
            with self.subTest(kind=data["kind"]):
                figure = figure_adapter.validate_python(data)
                self.assertEqual(figure_adapter.validate_json(figure.model_dump_json()), figure)

    def test_union_discriminator_required(self):
        for data in ({"kind": "photo"}, {"id": "missing-kind"}):
            with self.subTest(data=data), self.assertRaises(ValidationError):
                figure_adapter.validate_python(data)

    def test_map_endpoint_and_bounds(self):
        map_ = figure_adapter.validate_python(fixture("figures.json")[0])
        self.assertEqual((map_.endpoint().x, map_.endpoint().y), (2, 1))
        self.assertEqual(map_.destination().id, "studio")
        for updates in ({"start": {"x": 3, "y": 0}},
                        {"directions": [{"direction": "south", "blocks": 1}]},
                        {"vertical_streets": ["Too few"]}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                map_.model_copy(update=updates)

    def test_route_destination_and_elapsed_time(self):
        route = figure_adapter.validate_python(fixture("figures.json")[1])
        self.assertEqual(route.destination("pond", 2).id, "studio")
        self.assertEqual(route.travel_minutes("pond", "studio"), 25)
        for start, ahead in (("unknown", 1), ("pond", 3), ("pond", -1), ("pond", True)):
            with self.subTest(start=start, ahead=ahead), self.assertRaises(ValueError):
                route.destination(start, ahead)
        with self.assertRaises(ValueError):
            route.travel_minutes("studio", "pond")

    def test_route_time_order_and_overnight(self):
        data = fixture("figures.json")[1]
        data["stops"][1]["arrival_minute"] = 400
        with self.assertRaises(ValidationError):
            figure_adapter.validate_python(data)
        data = fixture("figures.json")[1]
        for stop in data["stops"]:
            stop["arrival_minute"] += 1000
            stop["departure_minute"] += 1000
        self.assertEqual(figure_adapter.validate_python(data).travel_minutes("pond", "studio"), 25)

    def test_table_query_and_shape(self):
        table = figure_adapter.validate_python(fixture("figures.json")[2])
        self.assertEqual(table.cell("box", "price"), "12")
        with self.assertRaises(ValueError):
            table.cell("missing", "price")
        with self.assertRaises(ValidationError):
            table.model_copy(update={"rows": [{"id": "x", "label": "X", "cells": []}]})

    def test_venn_intersection_and_regions(self):
        venn = figure_adapter.validate_python(fixture("figures.json")[3])
        self.assertEqual(venn.members("left_only"), ("Mira",))
        self.assertEqual(venn.members("intersection"), ("Owen",))
        self.assertEqual(venn.members("union"), ("Mira", "Owen", "Zia"))
        for updates in ({"intersection": []}, {"left": ["Mira", "Mira", "Owen"]}):
            with self.subTest(updates=updates), self.assertRaises(ValidationError):
                venn.model_copy(update=updates)


class BatchTests(unittest.TestCase):
    def test_synthetic_batch_roundtrip(self):
        batch = Batch.model_validate(fixture("synthetic_batch.json"))
        self.assertEqual(len(batch.all_items()), 1)
        self.assertEqual(Batch.model_validate_json(batch.model_dump_json()), batch)

    def test_validate_blueprint_and_catalog(self):
        batch = Batch(run_id="run", section_id="choice", produced_by="fixture", items=[item()])
        lesson = Lesson.model_validate(fixture("synthetic_lesson.json"))
        self.assertIs(batch.validate_against(small_blueprint(), lessons=(lesson,)), batch)

    def test_prevent_out_of_scope_target(self):
        batch = Batch(run_id="run", section_id="choice", produced_by="fixture",
                      items=[item(targets={"vocab_ids": ["moe2000-basic-word"]})])
        with self.assertRaisesRegex(ValueError, "targets"):
            batch.validate_against(small_blueprint(), lessons=(Lesson.model_validate(fixture("synthetic_lesson.json")),))

    def test_wrong_count_number_kind_or_figure(self):
        bp = small_blueprint()
        figures = fixture("figures.json")
        for questions in ([item(number=2)], [item(figure=figures[0])],
                          [item(), item(id="q2", number=2)]):
            with self.subTest(questions=questions), self.assertRaises(ValueError):
                Batch(run_id="run", section_id="choice", produced_by="fixture", items=questions).validate_against(bp)

    def test_empty_mixed_and_duplicate_batches_rejected(self):
        base = dict(run_id="run", section_id="choice", produced_by="fixture")
        group = Group.model_validate(fixture("synthetic_group.json"))
        for contents in ({}, {"items": [item(), item()]}, {"items": [item()], "groups": [group]},
                         {"items": [item(section_id="other")]}, {"items": [item(kind="reading")]}):
            with self.subTest(contents=contents), self.assertRaises(ValidationError):
                Batch(**base, **contents)

    def test_group_sizes_and_figures_match_blueprint(self):
        bp = small_blueprint(id="reading", kind="reading", group_count=1, items_per_group=[1], allow_figures=True)
        group = Group(id="g", section_id="reading", kind="reading", instruction="Read.",
                      figure=fixture("figures.json")[0], items=[item(section_id="reading", kind="reading")])
        batch = Batch(run_id="r", section_id="reading", produced_by="fixture", groups=[group])
        self.assertIs(batch.validate_against(bp), batch)
        with self.assertRaises(ValueError):
            batch.validate_against(small_blueprint(id="reading", kind="reading", group_count=1,
                                                  items_per_group=[1], allow_figures=False))


class WordListTests(unittest.TestCase):
    def write_files(self, folder, *, flag="true", count=2, headers="headword,pos,basic_1200,note"):
        csv_path = Path(folder) / "sample.csv"
        csv_path.write_text(f'{headers}\npaint,v.,{flag},手寫假資料\nbox,n.,false,"假資料,第二列"\n', encoding="utf-8-sig")
        csv_path.with_suffix(".meta.yaml").write_text(
            'source_url: https://example.invalid/synthetic\nversion: "fixture-v1"\n'
            f'retrieved_date: 2026-10-15\nusage_terms: 手寫假資料\nentry_count: {count}\n', encoding="utf-8")
        return csv_path

    def test_csv_yaml_loader_and_pending_state(self):
        with tempfile.TemporaryDirectory() as folder:
            words = load_wordlist(self.write_files(folder))
        self.assertEqual(words.id, "sample")
        self.assertEqual(words.status, "pending")
        self.assertTrue(words.entries[0].basic_1200)
        self.assertFalse(words.entries[1].basic_1200)
        self.assertEqual(words.entries[1].note, "假資料,第二列")
        self.assertEqual(WordList.model_validate_json(words.model_dump_json()), words)

    def test_bad_boolean_count_and_header(self):
        for options in ({"flag": "maybe"}, {"count": 3}, {"headers": "word,pos,basic_1200,note"}):
            with self.subTest(options=options), tempfile.TemporaryDirectory() as folder:
                with self.assertRaises(ValueError):
                    load_wordlist(self.write_files(folder, **options))

    def test_metadata_aliases_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_files(folder)
            path.with_suffix(".meta.yaml").write_text(
                'source_url: https://example.invalid/synthetic\n版本: "測試"\n取得日期: 2026-10-15\n'
                '使用條件: 手寫假資料\n筆數: 2\n', encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_wordlist(path, wordlist_id="explicit")

    def test_extended_provenance_and_basic_count(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_files(folder)
            meta_path = path.with_suffix(".meta.yaml")
            meta = meta_path.read_text(encoding="utf-8")
            extra = ('source_document: 手寫示範字表\nbasic_1200_count: 1\n'
                     'method: 人工編寫\nknown_issues:\n  - 僅供測試，不是官方字表\n')
            meta_path.write_text(meta + extra, encoding="utf-8")
            words = load_wordlist(path)
            self.assertEqual(words.metadata.basic_1200_count, 1)
            self.assertEqual(words.metadata.known_issues, ("僅供測試，不是官方字表",))
            meta_path.write_text(meta + extra.replace("basic_1200_count: 1", "basic_1200_count: 2"), encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "basic_1200 count"):
                load_wordlist(path)

    def test_duplicate_rows_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_files(folder)
            path.write_text("headword,pos,basic_1200,note\npaint,v.,true,a\npaint,v.,false,b\n", encoding="utf-8")
            with self.assertRaises(ValidationError):
                load_wordlist(path)

    def test_malformed_rows_and_unsafe_yaml_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = self.write_files(folder)
            path.write_text("headword,pos,basic_1200,note\npaint,v.,true\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_wordlist(path)
            path = self.write_files(folder)
            path.with_suffix(".meta.yaml").write_text("- not-a-mapping\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                load_wordlist(path)


class SchemaTests(unittest.TestCase):
    def test_export_is_deterministic_and_has_discriminator(self):
        with tempfile.TemporaryDirectory() as folder:
            paths = export_schemas(folder)
            first = {p.name: p.read_bytes() for p in paths}
            self.assertEqual(len(first), 16)
            export_schemas(folder)
            self.assertEqual(first, {p.name: p.read_bytes() for p in paths})
            schema = json.loads(first["figure.json"])
            self.assertEqual(schema["discriminator"]["propertyName"], "kind")
            self.assertEqual(set(schema["discriminator"]["mapping"]), {"street_map", "route", "table", "venn"})
            inputs = json.loads(first["item.json"])
            output = json.loads(first["item.output.json"])
            self.assertNotIn("content_hash", inputs["properties"])
            self.assertTrue(output["properties"]["content_hash"]["readOnly"])
            self.assertTrue(output["properties"]["status"]["readOnly"])

    def test_export_project_artifacts(self):
        # Intentional integration output: the allowed `agora check` command also
        # produces the requested schemas/ artifacts without changing its config.
        paths = export_schemas(ROOT / "schemas")
        for path in paths:
            with self.subTest(path=path.name):
                schema = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")


if __name__ == "__main__":
    unittest.main()
