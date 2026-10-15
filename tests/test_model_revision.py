"""M0b contract regression tests; also collected by unittest via agora check."""

import json
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pydantic import ValidationError

from dili.models import (
    Arrival, Blueprint, Item, Lesson, StreetMap, WordEntry, WordList,
    WordListMetadata, load_wordlist,
)


def fixture(name):
    return json.loads((ROOT / "examples" / name).read_text(encoding="utf-8"))


def place(id, slot, *, block=(1, 1), side="N", kind="building"):
    return dict(id=id, name=id, kind=kind, block=dict(x=block[0], y=block[1]),
                side=side, slot=slot)


def town(places=None, **updates):
    data = dict(id="town", title="手寫假地圖", width=3, height=3, slots_per_side=5,
                vertical_streets=["V0", "V1", "V2", "V3"],
                horizontal_streets=["H0", "H1", "H2", "H3"],
                places=places if places is not None else [place("a", 1)],
                start=dict(x=1, y=1), facing="north")
    return StreetMap.model_validate(data | updates)


class PartTests(unittest.TestCase):
    def test_mixed_group_part_sums_and_order(self):
        blueprint = Blueprint.model_validate(fixture("term_blueprint.json"))
        parts = blueprint.parts()
        self.assertEqual(len(parts), 4)
        group = parts[1]
        self.assertEqual(group.title, "第二部分、題組")
        self.assertEqual([s.id for s in group.sections], ["cloze", "reading"])
        self.assertEqual(group.total_items, 20)
        self.assertEqual(group.total_points, Decimal("30"))
        self.assertEqual(group.points_per_item, Decimal("1.5"))
        self.assertEqual(sum(p.total_points for p in parts), blueprint.total_points)
        self.assertEqual(sum(p.total_items for p in parts), blueprint.total_items)
        self.assertEqual(group.model_dump(mode="json")["total_points"], "30.0")

    def test_different_scores_have_no_single_score(self):
        data = fixture("term_blueprint.json")
        data["sections"][2]["points_per_item"] = "2"
        data["total_points"] = "107"
        group = Blueprint.model_validate(data).parts()[1]
        self.assertEqual(group.total_items, 20)
        self.assertEqual(group.total_points, Decimal("37"))
        self.assertIsNone(group.points_per_item)

    def test_none_never_merges_even_with_same_title(self):
        data = fixture("term_blueprint.json")
        for section in data["sections"]:
            section.pop("part", None)
            section["title"] = "相同標題"
        blueprint = Blueprint.model_validate(data)
        self.assertEqual(len(blueprint.parts()), 5)
        self.assertTrue(all(len(p.sections) == 1 for p in blueprint.parts()))

    def test_repeated_part_after_none_or_another_name_rejected(self):
        for middle in (None, "另一部分"):
            data = fixture("term_blueprint.json")
            for section, part in zip(data["sections"], ["甲", middle, "甲", None, None]):
                section["part"] = part
            with self.subTest(middle=middle), self.assertRaisesRegex(ValidationError, "contiguous"):
                Blueprint.model_validate(data)


class PolarityAndHeadingTests(unittest.TestCase):
    def test_all_choice_kinds_accept_all_polarities(self):
        for kind in ("single_choice", "cloze", "reading"):
            for polarity in ("best", "wrong", "correct"):
                with self.subTest(kind=kind, polarity=polarity):
                    data = fixture("synthetic_item.json") | dict(kind=kind, polarity=polarity)
                    question = Item.model_validate(data)
                    self.assertEqual(question.polarity, polarity)
                    self.assertEqual(Item.model_validate_json(question.model_dump_json()), question)
        self.assertEqual(Item.model_validate(fixture("synthetic_item.json")).polarity, "best")

    def test_non_choice_rejects_wrong_and_correct(self):
        for kind, task in (("word_in_context", "word_in_context"), ("guided_writing", "rewrite")):
            data = fixture("synthetic_item.json") | dict(
                kind=kind, choices=None, prompt=dict(task=task, instruction="手寫題目"),
                answer=dict(accepted_text=["painting"]), explanation=dict(correct_zh="作答說明"))
            self.assertEqual(Item.model_validate(data).polarity, "best")
            for polarity in ("wrong", "correct"):
                with self.subTest(kind=kind, polarity=polarity), self.assertRaisesRegex(ValidationError, "polarity"):
                    Item.model_validate(data | dict(polarity=polarity))

    def test_wrong_question_explains_why_true_statements_are_unselected(self):
        question = Item.model_validate(fixture("synthetic_wrong_item.json"))
        self.assertEqual(question.polarity, "wrong")
        self.assertEqual(question.answer.choice, "B")
        self.assertEqual({o.option for o in question.explanation.distractors}, {"A", "C", "D"})
        self.assertTrue(all("正確" in o.reason_zh for o in question.explanation.distractors))

    def test_polarity_change_invalidates_approval(self):
        question = Item.model_validate(fixture("synthetic_item.json"))
        question = question.add_review(reviewer="fixture", role="human", decision="approve", notes="測試")
        changed = question.model_copy(update=dict(polarity="wrong"))
        self.assertNotEqual(changed.content_hash, question.content_hash)
        self.assertEqual(changed.status, "pending")

    def test_heading_can_be_absent_or_none_but_not_empty(self):
        for omitted in (True, False):
            data = fixture("synthetic_lesson.json")
            if omitted:
                data["vocab"][0].pop("category_heading")
            else:
                data["vocab"][0]["category_heading"] = None
            self.assertIsNone(Lesson.model_validate(data).vocab[0].category_heading)
        data["vocab"][0]["category_heading"] = "   "
        with self.assertRaises(ValidationError):
            Lesson.model_validate(data)


class WordListRevisionTests(unittest.TestCase):
    def test_case_sensitive_headword_and_pos(self):
        metadata = WordListMetadata(source_url="https://example.invalid", version="fixture",
                                    retrieved_date="2026-10-16", usage_terms="手寫假資料", entry_count=3)
        entries = [WordEntry(headword="Miss", pos="", basic_1200=True),
                   WordEntry(headword="miss", pos="", basic_1200=True),
                   WordEntry(headword="miss", pos="V.", basic_1200=True)]
        words = WordList(id="fixture", metadata=metadata, entries=entries)
        self.assertEqual([e.headword for e in words.entries], ["Miss", "miss", "miss"])
        with self.assertRaisesRegex(ValidationError, "duplicate headword/POS"):
            words.model_copy(update=dict(entries=[entries[0], entries[1], entries[1]]))
        words.model_copy(update=dict(entries=[entries[2], entries[2].model_copy(update=dict(pos="v.")), entries[0]]))

    def test_miss_and_Miss_csv_loader(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "fixture.csv"
            path.write_text("headword,pos,basic_1200,note\nMiss,,true,稱謂\nmiss,,true,動詞\n", encoding="utf-8")
            path.with_suffix(".meta.yaml").write_text(
                "source_url: https://example.invalid\nversion: fixture\nretrieved_date: 2026-10-16\n"
                "usage_terms: 手寫假資料\nentry_count: 2\n", encoding="utf-8")
            self.assertEqual(len(load_wordlist(path).entries), 2)
            path.write_text("headword,pos,basic_1200,note\nMiss,,true,稱謂\nMiss,,true,重複\n", encoding="utf-8")
            with self.assertRaisesRegex(ValidationError, "duplicate headword/POS"):
                load_wordlist(path)

    def test_only_standard_metadata_keys(self):
        standard = dict(source_url="https://example.invalid", source_document="示範", version="fixture",
                        retrieved_date="2026-10-16", usage_terms="假資料", entry_count=2,
                        basic_1200_count=1, method="手寫", known_issues=["測試"])
        aliases = dict(source_document=["source_title", "document_title", "來源文件名稱"],
                       version=["版本"], retrieved_date=["retrieved_at", "retrieval_date", "accessed_date", "取得日期"],
                       usage_terms=["license", "使用條件"],
                       entry_count=["count", "total_entries", "total_count", "筆數", "總筆數"],
                       basic_1200_count=["basic_count", "1200筆數"],
                       method=["methodology", "processing_method", "整理方式"], known_issues=["已知問題"])
        self.assertEqual(set(WordListMetadata.model_fields), set(standard))
        for key, old_names in aliases.items():
            for alias in old_names:
                data = dict(standard)
                data[alias] = data.pop(key)
                with self.subTest(alias=alias), self.assertRaises(ValidationError):
                    WordListMetadata.model_validate(data)

    def test_official_wordlist_integration(self):
        words = load_wordlist(ROOT / "data/wordlists/moe2000.csv")
        self.assertEqual(len(words.entries), 2005)
        self.assertEqual(sum(e.basic_1200 for e in words.entries), 1211)
        self.assertEqual(words.metadata.entry_count, 2005)
        self.assertEqual(words.metadata.basic_1200_count, 1211)
        self.assertTrue({("Miss", ""), ("miss", "")} <= {(e.headword, e.pos) for e in words.entries})
        self.assertEqual(words.status, "pending")


class MapRelationTests(unittest.TestCase):
    def test_on_street_and_corner_for_all_sides_and_ends(self):
        expected = {"N": ("H2", ("H2", "V1"), ("H2", "V2")),
                    "S": ("H1", ("H1", "V1"), ("H1", "V2")),
                    "E": ("V2", ("H1", "V2"), ("H2", "V2")),
                    "W": ("V1", ("H1", "V1"), ("H2", "V1"))}
        for side, (street, low, high) in expected.items():
            with self.subTest(side=side):
                m = town([place("low", 0, side=side), place("mid", 2, side=side), place("high", 4, side=side)])
                self.assertEqual(m.on_street("mid"), street)
                self.assertNotEqual(m.on_street("mid"), "H0")
                self.assertEqual(m.corner_of("low"), low)
                self.assertEqual(m.corner_of("high"), high)
                self.assertIsNone(m.corner_of("mid"))

    def test_next_to_and_between_need_consecutive_buildings(self):
        m = town([place("a", 0), place("b", 1), place("c", 2), place("far", 4),
                  place("stop", 2, kind="stop"), place("other", 2, side="S")])
        self.assertTrue(m.next_to("a", "b"))
        self.assertTrue(m.next_to("b", "a"))
        for pair in (("a", "c"), ("c", "far"), ("b", "stop"), ("b", "other"), ("b", "b")):
            with self.subTest(pair=pair):
                self.assertFalse(m.next_to(*pair))
        self.assertTrue(m.between("b", "a", "c"))
        self.assertTrue(m.between("b", "c", "a"))
        for triple in (("a", "b", "c"), ("c", "b", "far"), ("b", "a", "stop"), ("b", "a", "other"), ("b", "a", "a")):
            with self.subTest(triple=triple):
                self.assertFalse(m.between(*triple))

    def test_across_from_all_four_directions(self):
        for side, opposite, block in (("N", "S", (1, 2)), ("S", "N", (1, 0)),
                                      ("E", "W", (2, 1)), ("W", "E", (0, 1))):
            with self.subTest(side=side):
                m = town([place("a", 2, side=side), place("b", 2, side=opposite, block=block),
                          place("offset", 1, side=opposite, block=block),
                          place("back", 2, side=opposite)])
                self.assertTrue(m.across_from("a", "b"))
                self.assertTrue(m.across_from("b", "a"))
                self.assertFalse(m.across_from("a", "offset"))
                self.assertFalse(m.across_from("a", "back"))
                self.assertFalse(m.across_from("a", "a"))

    def test_same_street_name_does_not_make_different_streets_across(self):
        m = town([place("a", 1, side="N", block=(1, 0)), place("b", 1, side="S", block=(1, 2))],
                 horizontal_streets=["Repeated"] * 4)
        self.assertEqual(m.on_street("a"), m.on_street("b"))
        self.assertFalse(m.across_from("a", "b"))

    def test_in_front_of_requires_same_facing_and_roles(self):
        m = town([place("a", 1), place("stop", 1, kind="stop"),
                  place("offset", 2, kind="stop"), place("back", 1, side="S", kind="stop")])
        self.assertTrue(m.in_front_of("stop", "a"))
        self.assertFalse(m.in_front_of("a", "stop"))
        self.assertFalse(m.in_front_of("offset", "a"))
        self.assertFalse(m.in_front_of("back", "a"))
        self.assertFalse(m.in_front_of("a", "a"))

    def test_queries_are_pure_and_reject_unknown_or_foreign_place(self):
        m = town([place("a", 0), place("b", 1), place("c", 2), place("stop", 1, kind="stop")])
        before = m.model_dump_json()
        self.assertEqual(m.on_street(m.places[0]), "H2")
        m.corner_of("a")
        m.next_to("a", "b")
        m.between("b", "a", "c")
        m.across_from("a", "b")
        m.in_front_of("stop", "b")
        self.assertEqual(m.model_dump_json(), before)
        with self.assertRaisesRegex(ValueError, "unknown place"):
            m.on_street("absent")
        with self.assertRaisesRegex(ValueError, "does not match"):
            m.corner_of(m.places[0].model_copy(update=dict(slot=4)))


class MapValidationTests(unittest.TestCase):
    def test_slots_and_blocks_strict_bounds(self):
        for k in (0, 1, True, 2.0, "3"):
            with self.subTest(k=k), self.assertRaises(ValidationError):
                town(slots_per_side=k)
        for slot in (-1, 5, True, 1.5):
            with self.subTest(slot=slot), self.assertRaises(ValidationError):
                town([place("bad", slot)])
        for block in ((3, 1), (1, 3), (-1, 1), (1, -1)):
            with self.subTest(block=block), self.assertRaises(ValidationError):
                town([place("bad", 0, block=block)])
        self.assertIsNotNone(town([place("edge", 1, block=(2, 2))], slots_per_side=2))

    def test_duplicate_buildings_in_same_slot_rejected(self):
        with self.assertRaisesRegex(ValidationError, "building positions"):
            town([place("a", 2), place("b", 2)])

    def test_cross_side_collisions_at_every_corner(self):
        for side_a, slot_a, side_b, slot_b in (("N", 0, "W", 4), ("N", 4, "E", 4),
                                               ("S", 0, "W", 0), ("S", 4, "E", 0)):
            with self.subTest(side_a=side_a, side_b=side_b), self.assertRaisesRegex(ValidationError, "building positions"):
                town([place("a", slot_a, side=side_a), place("b", slot_b, side=side_b)])

    def test_corner_buildings_in_different_blocks_and_stop_overlap_allowed(self):
        m = town([place("a", 0), place("b", 0, block=(1, 2), side="S"),
                  place("stop", 0, kind="stop")])
        self.assertTrue(m.across_from("a", "b"))
        self.assertTrue(m.in_front_of("stop", "a"))

    def test_old_intersection_places_rejected(self):
        with self.assertRaises(ValidationError):
            town([dict(id="old", name="Old", position=dict(x=1, y=1))])

    def test_arrival_ordinal_is_positive_strict_integer(self):
        for position in (0, -1, True, 1.5, "1", "first"):
            with self.subTest(position=position), self.assertRaises(ValidationError):
                Arrival(side="left", position=position)


class MapArrivalTests(unittest.TestCase):
    # Expected last block/facing side derived independently for each final heading.
    CASES = [
        ("north", "left", (0, 1), "E", 4), ("north", "right", (1, 1), "W", 4),
        ("south", "left", (1, 0), "W", 0), ("south", "right", (0, 0), "E", 0),
        ("east", "left", (1, 1), "S", 4), ("east", "right", (1, 0), "N", 4),
        ("west", "left", (0, 0), "N", 0), ("west", "right", (0, 1), "S", 0),
    ]

    def test_corner_and_ordinal_all_headings_and_sides(self):
        for direction, arrival_side, block, side, corner in self.CASES:
            buildings = [place("corner", corner, block=block, side=side),
                         place("inner", 2, block=block, side=side)]
            for position, expected in (("corner", "corner"), (1, "corner"), (2, "inner")):
                with self.subTest(direction=direction, side=arrival_side, position=position):
                    m = town(buildings, directions=[dict(direction=direction, blocks=1)],
                             facing="west", arrival=dict(side=arrival_side, position=position))
                    self.assertEqual(m.destination().id, expected)
                    self.assertEqual(StreetMap.model_validate_json(m.model_dump_json()), m)

    def test_ordinals_skip_empty_slots_and_stops_in_both_orders(self):
        for direction, arrival_side, block, side, corner in self.CASES:
            buildings = [place("low", 1, block=block, side=side), place("high", 3, block=block, side=side),
                         place("stop", corner, block=block, side=side, kind="stop"),
                         place("front", 3, block=block, side=side, kind="stop")]
            for position, expected in ((1, "high" if corner == 4 else "low"),
                                       (2, "low" if corner == 4 else "high")):
                with self.subTest(direction=direction, side=arrival_side, position=position):
                    m = town(buildings, directions=[dict(direction=direction, blocks=1)],
                             arrival=dict(side=arrival_side, position=position))
                    self.assertEqual(m.destination().id, expected)
                    for invalid in ("corner", 3):
                        with self.assertRaisesRegex(ValidationError, "exactly one building"):
                            m.model_copy(update=dict(arrival=dict(side=arrival_side, position=invalid)))

    def test_uses_last_segment_after_turn_and_only_final_block(self):
        m = town([place("target", 2, block=(1, 0), side="E"),
                  place("after", 4, block=(1, 1), side="E"),
                  place("earlier", 4, block=(0, 0), side="S")],
                 start=dict(x=0, y=0), facing="south",
                 directions=[dict(direction="east", blocks=2), dict(direction="north", blocks=1)],
                 arrival=dict(side="left", position=1))
        self.assertEqual(m.destination().id, "target")
        # Even a multi-block final step must not count a building in an earlier block.
        m = town([place("target", 1, block=(1, 1), side="E"),
                  place("earlier", 4, block=(1, 0), side="E")], start=dict(x=2, y=0),
                 directions=[dict(direction="north", blocks=2)], arrival=dict(side="left", position=1))
        self.assertEqual(m.destination().id, "target")
        with self.assertRaisesRegex(ValidationError, "exactly one building"):
            m.model_copy(update=dict(arrival=dict(side="left", position=2)))

    def test_empty_path_with_arrival_rejected_but_optional_without_arrival(self):
        m = town()
        self.assertEqual(m.endpoint(), m.start)
        with self.assertRaisesRegex(ValueError, "requires an arrival"):
            m.destination()
        with self.assertRaisesRegex(ValidationError, "at least one MapStep"):
            town(arrival=dict(side="left", position=1))

    def test_other_facing_at_endpoint_corner_is_not_a_candidate(self):
        with self.assertRaisesRegex(ValidationError, "exactly one building"):
            town([place("wrong-facing", 4, block=(0, 1), side="N")],
                 directions=[dict(direction="north", blocks=1)], arrival=dict(side="left", position="corner"))

    def test_outer_edge_and_no_building_candidate_rejected(self):
        with self.assertRaisesRegex(ValidationError, "exactly one building"):
            town(start=dict(x=0, y=1), directions=[dict(direction="north", blocks=1)],
                 arrival=dict(side="left", position=1))
        with self.assertRaisesRegex(ValidationError, "exactly one building"):
            town([place("stop", 4, side="E", block=(0, 1), kind="stop")],
                 directions=[dict(direction="north", blocks=1)], arrival=dict(side="left", position="corner"))

    def test_out_of_grid_intermediate_step_rejected_even_if_endpoint_returns(self):
        with self.assertRaisesRegex(ValidationError, "leaves the street grid"):
            town(directions=[dict(direction="north", blocks=3), dict(direction="south", blocks=3)])


if __name__ == "__main__":
    unittest.main()
