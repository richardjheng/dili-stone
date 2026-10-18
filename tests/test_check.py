"""M2 contract tests. All fixtures below are handwritten synthetic content."""

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dili.check import Lexicon, check_exam, compare_exams, expand_headword, figure_texts
from dili.cli import main
from dili.models import Batch, Blueprint, Group, Item, Lesson, WordList, figure_adapter


def lesson(id="lesson", heads=None):
    heads = heads or [("bake", "application"), ("run", "application"),
                      ("cat", "application"), ("dog", "application"), ("bird", "recognition")]
    return Lesson.model_validate(dict(
        id=id, publisher="假出版社", volume=1, unit=1, title="自寫假資料", academic_year="115",
        completeness="complete", sources=[dict(id="source", filename="synthetic", usage_terms="自行撰寫")],
        vocab=[dict(id=f"{id}-v{n}", headword=head, pos=["n."],
                    senses=[dict(id="sense", meaning_zh="測試字義")],
                    textbook_category=category, source_refs=["source"])
               for n, (head, category) in enumerate(heads)],
        grammar=[dict(id=f"{id}-grammar", name="測試句型", description="自寫假句型", source_refs=["source"])]))


def words(heads=("the", "a/an", "be (am, is, are, was, were, been)", "see", "small", "and")):
    return WordList.model_validate(dict(
        id="moe2000", metadata=dict(source_url="https://example.invalid/synthetic",
                                   version="fake", retrieved_date="2026-10-16", usage_terms="手寫假資料",
                                   entry_count=len(heads)),
        entries=[dict(headword=h, pos="", basic_1200=True) for h in heads]))


def blueprint(n=1, kind="single_choice", **updates):
    section = dict(id="s", title="假大題", kind=kind, item_count=n, points_per_item="2", allow_figures=True)
    if kind in {"reading", "cloze"}:
        section.update(group_count=1, items_per_group=[n])
    return Blueprint.model_validate(dict(
        id="bp", exam=dict(title="假考卷", exam_type="quiz", academic_year="115", semester=1, duration_minutes=10),
        header=dict(school_name="假學校", grade=8, scope_text="假單元"), scope=["lesson"],
        prerequisite=dict(wordlist_id="moe2000", lesson_ids=[]), sections=[section],
        total_items=n, total_points=str(n * 2)) | updates)


def item(n=1, kind="single_choice", **updates):
    data = dict(id=f"q{n}", section_id="s", kind=kind, number=n, stem="the cat",
                targets=dict(vocab_ids=["lesson-v0"]))
    if kind in {"single_choice", "reading", "cloze"}:
        data.update(choices=dict(A="bake", B="run", C="cat", D="dog"), answer=dict(choice="A"),
                    explanation=dict(correct_zh="手寫解析", distractors=[
                        dict(option=c, reason_zh="自寫說明") for c in "BCD"]))
    else:
        data.update(prompt=dict(task="word_in_context" if kind == "word_in_context" else "rewrite",
                                instruction="請作答"), answer=dict(accepted_text=["baked"]),
                    explanation=dict(correct_zh="手寫解析"))
    return Item.model_validate(data | updates)


def batch(items=None, **updates):
    return Batch.model_validate(dict(run_id="run", section_id="s", produced_by="fixture",
                                     items=[item()] if items is None else items) | updates)


def check(items=None, *, bp=None, batches=None, lessons=None, wordlist=None, **kwargs):
    items = [item()] if items is None else items
    return check_exam(bp or blueprint(len(items), items[0].kind), batches if batches is not None else [batch(items)],
                      lessons=lessons if lessons is not None else [lesson()], wordlist=wordlist or words(), **kwargs)


def codes(report, severity="errors"):
    return {f.code for f in getattr(report, severity)}


class LexicalTests(unittest.TestCase):
    def test_layers_and_precedence(self):
        lex = Lexicon(["cat"], ["cat", "dog"], ["cat", "dog", "yak"])
        self.assertEqual([m.layer for m in lex.scan("cat dog yak quokka")],
                         ["scope", "prerequisite", "glossed", "outside"])

    def test_regular_morphology(self):
        pairs = {"cats": "cat", "boxes": "box", "studies": "study", "studied": "study",
                 "walked": "walk", "stopped": "stop", "running": "run", "baking": "bake",
                 "baked": "bake", "happier": "happy", "happiest": "happy", "bigger": "big",
                 "biggest": "big", "nicer": "nice", "nicest": "nice", "dying": "die",
                 "cat's": "cat", "cats'": "cat", "cat’s": "cat"}
        for form, base in pairs.items():
            with self.subTest(form=form):
                self.assertEqual(Lexicon([base]).scan(form)[0].candidates, ((base, "scope"),))

    def test_irregular_morphology(self):
        for form, base in [("went", "go"), ("written", "write"), ("children", "child"),
                           ("feet", "foot"), ("knives", "knife"), ("worse", "bad")]:
            with self.subTest(form=form):
                self.assertTrue(Lexicon([base]).is_target(form))

    def test_bracket_and_slash_expansion(self):
        for head, forms in [("be (am, is, are, was, were, been)", ["be", "am", "were", "been"]),
                            ("airplane (plane)", ["airplane", "plane"]),
                            ("shoe (s)", ["shoe", "shoes"]), ("a/an", ["a", "an"])]:
            lex = Lexicon([head])
            for form in forms:
                with self.subTest(head=head, form=form):
                    self.assertTrue(lex.is_target(form))
        self.assertNotIn("s", expand_headword("shoe (s)")[1])

    def test_longest_phrase_and_possessive_placeholder(self):
        lex = Lexicon(["make fun of", "get butterflies in one's stomach"], ["make fun", "make", "fun", "of"])
        for text in ["get butterflies in my stomach", "got butterflies in her stomach",
                     "get butterflies in Alex's stomach"]:
            with self.subTest(text=text):
                self.assertTrue(lex.is_target(text))
        match = lex.scan("made fun of")[0]
        self.assertEqual(match.candidates, (("make fun of", "scope"),))
        self.assertFalse(lex.is_target("made. fun of"))
        self.assertFalse(lex.is_target("made ___ fun of"))

    def test_phrase_components_do_not_become_whitelisted(self):
        lex = Lexicon(["get butterflies in one's stomach"])
        self.assertFalse(lex.is_target("stomach"))

    def test_ambiguity_does_not_choose_in_scope_candidate(self):
        lex = Lexicon(["saw"], ["see"])
        self.assertEqual(lex.scan("saw")[0].layer, "ambiguous")
        self.assertFalse(lex.is_target("saw"))
        # Explicit irregular candidates survive even when absent from both lists.
        self.assertEqual(Lexicon(["good"]).scan("better")[0].layer, "ambiguous")

    def test_numbers_and_mid_sentence_names_only(self):
        matches = Lexicon(["cat"]).scan("cat Alice 12.5. Quokka")
        self.assertEqual([m.kind for m in matches], ["word", "proper_name", "number", "word"])
        self.assertEqual(matches[-1].layer, "outside")
        self.assertEqual(Lexicon(["alice"]).scan("cat Alice")[-1].layer, "scope")


    def test_contractions_resolve_to_host_words(self):
        lex = Lexicon(["cat"], ["do (does, did, done)", "can", "will", "it", "we", "be (am, is, are)"])
        for text in ("didn't", "can't", "won't", "it's", "we're", "we'll"):
            with self.subTest(text=text):
                self.assertEqual(lex.scan(text)[0].layer, "prerequisite")

    def test_listed_spelling_is_not_split_by_suffix_rules(self):
        lex = Lexicon(["corner"], ["corn"])
        self.assertEqual(lex.scan("corner")[0].candidates, (("corner", "scope"),))
        self.assertEqual(lex.scan("corners")[0].layer, "scope")

    def test_listed_names_are_proper_names_even_at_sentence_start(self):
        matches = Lexicon(["cat"], names=["Tom"]).scan("Tom has a cat. Tom's cat. Quokka")
        self.assertEqual(matches[0].kind, "proper_name")
        self.assertEqual(matches[4].kind, "proper_name")
        self.assertEqual(matches[-1].kind, "word")
        self.assertEqual(matches[-1].layer, "outside")


class ExamTests(unittest.TestCase):
    def test_reading_vocab_targets_allow_contextual_answers_but_check_words(self):
        def reading_report(option):
            question = item(kind="reading", choices=dict(
                A=option, B="a small cat", C="the dog", D="the bird"))
            group = Group(id="g", section_id="s", kind="reading", instruction="請閱讀",
                          passage="the cat and the dog", items=(question,))
            return check(bp=blueprint(kind="reading"),
                         batches=[batch([], groups=[group])])

        self.assertEqual(reading_report("the small cat").errors, [])
        report = reading_report("the quokka")
        self.assertIn("out_of_scope", codes(report))
        self.assertNotIn("option_not_scope", codes(report))

    def test_cloze_vocab_options_still_require_in_scope_headwords(self):
        question = item(kind="cloze", choices=dict(A="the cat", B="run", C="cat", D="dog"))
        group = Group(id="g", section_id="s", kind="cloze", instruction="請填空",
                      passage="the {{1}}", items=(question,))
        report = check(bp=blueprint(kind="cloze"), batches=[batch([], groups=[group])])
        self.assertIn("option_not_scope", codes(report))

    def test_clean_exam_and_report_contract(self):
        report = check()
        self.assertEqual(report.errors, [])
        self.assertEqual(set(report.to_dict()), {"errors", "warnings", "infos", "pending_count"})
        self.assertEqual(set(report.to_dict()["infos"][0]), {"code", "item_id", "number", "message", "words"})

    def test_unknown_and_local_gloss(self):
        report = check([item(stem="quokka", glossed=[dict(word="quokka", meaning_zh="假字義")]),
                        item(2, stem="quokka")])
        errors = [e for e in report.errors if e.code == "out_of_scope"]
        self.assertEqual([(e.number, e.words) for e in errors], [(2, ["quokka"])])
        self.assertIn("glossed", codes(report, "infos"))

    def test_prerequisite_lesson_is_context_only(self):
        bp = blueprint(prerequisite=dict(wordlist_id="moe2000", lesson_ids=["prior"]))
        report = check([item(stem="jump", choices=dict(A="bake", B="run", C="cat", D="jump"))],
                       bp=bp, lessons=[lesson(), lesson("prior", [("jump", "application")])])
        self.assertIn("option_not_scope", codes(report))
        self.assertNotIn("out_of_scope", codes(report))

    def test_vocab_options_require_whole_entry(self):
        report = check([item(choices=dict(A="the", B="run cat", C="bakes", D="dog"))])
        self.assertEqual([e.words for e in report.errors if e.code == "option_not_scope"], [["the"], ["run cat"]])

    def test_grammar_choice_exemption(self):
        report = check([item(choices=dict(A="the", B="run", C="cat", D="dog"),
                             targets=dict(vocab_ids=["lesson-v0"], grammar_ids=["lesson-grammar"]))])
        self.assertNotIn("option_not_scope", codes(report))

    def test_ambiguous_context_warning_and_option_error(self):
        heads = [("saw", "application"), ("run", "application"), ("cat", "application"), ("dog", "application")]
        # saw (scope) / see (prerequisite): every reading is allowed in context, but an
        # option must still be unambiguously in scope.
        report = check([item(stem="saw", choices=dict(A="saw", B="run", C="cat", D="dog"))],
                       lessons=[lesson(heads=heads)])
        self.assertNotIn("ambiguous_lemma", codes(report, "warnings"))
        self.assertIn("option_not_scope", codes(report))
        # better → good (scope) / well (outside): an out-of-range reading must be reviewed.
        report = check([item(stem="better")], lessons=[lesson(heads=[("good", "application"), *heads[1:]])])
        warning = next(w for w in report.warnings if w.code == "ambiguous_lemma")
        self.assertIn("good", warning.words)
        self.assertIn("well", warning.words)

    def test_application_answers_and_each_hint(self):
        q = item(kind="word_in_context", answer=dict(accepted_text=["baked", "bird", "the"]),
                 prompt=dict(task="word_in_context", instruction="請作答", first_letter="B", last_letter="d"))
        report = check([q])
        self.assertEqual([e.words for e in report.errors if e.code == "answer_not_application"], [["bird"], ["the"]])
        self.assertEqual(len([e for e in report.errors if e.code == "letter_hint_mismatch"]), 2)
        self.assertNotIn("answer_not_application", codes(check([item(kind="word_in_context")])))

    def test_first_and_last_hints_are_checked_against_surface_answer(self):
        q = item(kind="word_in_context", prompt=dict(task="word_in_context", instruction="請作答",
                                                      first_letter="x", last_letter="e"))
        self.assertEqual(len([e for e in check([q]).errors if e.code == "letter_hint_mismatch"]), 2)

    def test_structure_missing_duplicate_and_unknown_sections(self):
        for batches, expected in [([], {"section_batch_count", "numbering", "total_points"}),
                                  ([batch(), batch()], {"section_batch_count", "numbering", "total_points", "duplicate_id"})]:
            self.assertTrue(expected <= codes(check(batches=batches)))
        q = item(section_id="foreign")
        self.assertIn("batch_structure", codes(check(batches=[batch([q], section_id="foreign")])))

    def test_bad_number_and_target_reference(self):
        self.assertTrue({"batch_structure", "numbering"} <= codes(check([item(2)])))
        self.assertIn("batch_structure", codes(check([item(targets=dict(vocab_ids=["missing"]))])))

    def test_missing_catalog_and_wrong_wordlist(self):
        self.assertIn("missing_lesson", codes(check(lessons=[])))
        self.assertIn("missing_lesson", codes(check(bp=blueprint(prerequisite=dict(wordlist_id="moe2000", lesson_ids=["absent"])))))
        self.assertIn("wordlist_mismatch", codes(check(wordlist=words().model_copy(update=dict(id="other")))))

    def test_duplicate_stems_choices_and_overused_target(self):
        report = check([item(i) for i in range(1, 5)])
        self.assertTrue({"similar_stem", "duplicate_choices", "target_overuse"} <= codes(report, "warnings"))
        self.assertNotIn("target_overuse", codes(check([item(i) for i in range(1, 5)], max_target_uses=4), "warnings"))
        near = [item(stem="the cat runs"), item(2, stem="the cat run")]
        self.assertIn("similar_stem", codes(check(near, similarity_threshold=0.8), "warnings"))
        self.assertNotIn("similar_stem", codes(check(near, similarity_threshold=1), "warnings"))

    def test_option_order_ignored_and_cross_exam_comparison(self):
        second = batch([item(choices=dict(A="dog", B="cat", C="run", D="bake"))])
        report = compare_exams([batch()], [second])
        self.assertTrue({"cross_exam_similar_stem", "cross_exam_duplicate_choices"} <= codes(report, "warnings"))
        self.assertEqual(report.pending_count, 0)

    def test_pending_is_read_only_and_counts_groups_separately(self):
        q = item(kind="reading")
        group = Group(id="group", section_id="s", kind="reading", instruction="請閱讀", passage="the cat", items=[q])
        b = batch([], groups=[group])
        before = b.model_dump_json()
        report = check(batches=[b], bp=blueprint(kind="reading"))
        self.assertEqual(report.pending_count, 2)
        self.assertEqual(b.model_dump_json(), before)
        reviewed = item().add_review(reviewer="fake", role="human", decision="approve", notes="手寫測試")
        self.assertEqual(check([reviewed]).pending_count, 0)

    def test_group_gloss_inheritance_without_child_to_parent_leak(self):
        group = Group(id="group", section_id="s", kind="reading", instruction="請閱讀",
                      passage="yak quokka", glossed=[dict(word="yak", meaning_zh="假字義")],
                      items=[item(kind="reading", stem="yak quokka", glossed=[dict(word="quokka", meaning_zh="假字義")])])
        report = check(batches=[batch([], groups=[group])], bp=blueprint(kind="reading"))
        errors = [e for e in report.errors if e.code == "out_of_scope"]
        self.assertEqual([(e.item_id, e.words) for e in errors], [("group", ["quokka"])])

    def test_answer_prompt_cues_checked_but_explanation_excluded(self):
        q = item(kind="guided_writing", stem="the cat", answer=dict(accepted_text=["quokka"]),
                 prompt=dict(task="rewrite", instruction="yak", cues=["zebra"]),
                 explanation=dict(correct_zh="中文解析 unicorn"))
        found = {w for e in check([q]).errors if e.code == "out_of_scope" for w in e.words}
        self.assertEqual(found, {"quokka", "yak", "zebra"})

    def test_cloze_boilerplate_stems_are_not_duplicates(self):
        group = dict(id="g", section_id="s", kind="cloze", instruction="the cat", passage="the {{1}} and {{2}}",
                     items=[item(n, kind="cloze", stem=f"the cat {n}") for n in (1, 2)])
        report = check(batches=[batch([], groups=[group])], bp=blueprint(2, kind="cloze"))
        self.assertNotIn("similar_stem", codes(report, "warnings"))

    def test_extended_800_words_need_a_gloss(self):
        wordlist = WordList.model_validate(dict(
            id="moe2000", metadata=dict(source_url="https://example.invalid/synthetic", version="fake",
                                       retrieved_date="2026-10-18", usage_terms="手寫假資料", entry_count=2),
            entries=[dict(headword="the", pos="", basic_1200=True), dict(headword="mall", pos="", basic_1200=False)]))
        report = check([item(stem="the cat mall")], wordlist=wordlist)
        finding = next(e for e in report.errors if e.code == "out_of_scope")
        self.assertIn("800", finding.message)
        glossed = check([item(stem="the cat mall", glossed=[dict(word="mall", meaning_zh="購物中心")])],
                        wordlist=wordlist)
        self.assertNotIn("out_of_scope", codes(glossed))

    def test_letter_hint_blanks_are_not_words(self):
        report = check([item(kind="word_in_context", stem="the cat b______e and the l____r ______")])
        self.assertNotIn("out_of_scope", codes(report))

    def test_cloze_placeholders_are_not_number_info(self):
        group = Group(id="group", section_id="s", kind="cloze", instruction="請填空",
                      passage="the {{1}} cat", items=[item(kind="cloze")])
        report = check(batches=[batch([], groups=[group])], bp=blueprint(kind="cloze"))
        self.assertNotIn("number", codes(report, "infos"))

    def test_threshold_validation(self):
        for kwargs in [dict(similarity_threshold=1.1), dict(max_target_uses=0), dict(max_target_uses=True)]:
            with self.assertRaises(ValueError):
                check(**kwargs)
        with self.assertRaises(ValueError):
            compare_exams([], [], similarity_threshold=-1)


class FigureTests(unittest.TestCase):
    def test_every_figure_visible_field(self):
        figures = [
            dict(kind="street_map", id="internal", title="titleword", width=1, height=1, slots_per_side=2,
                 vertical_streets=["vfirst", "vsecond"], horizontal_streets=["hfirst", "hsecond"],
                 places=[dict(id="internal-place", name="placename", kind="building", block=dict(x=0, y=0), side="N", slot=0)],
                 start=dict(x=0, y=0), facing="north"),
            dict(kind="route", id="internal", title="titleword", service_day="dayword",
                 stops=[dict(id="a", name="stopfirst", arrival_minute=0, departure_minute=1),
                        dict(id="b", name="stopsecond", arrival_minute=2, departure_minute=3)]),
            dict(kind="table", id="internal", title="titleword", purpose="general",
                 columns=[dict(id="c", label="columnword", unit="unitword")],
                 rows=[dict(id="r", label="rowword", cells=["cellword"])]),
            dict(kind="venn", id="internal", title="titleword", left_label="leftlabel", right_label="rightlabel",
                 left=["leftmember", "sharedmember"], right=["rightmember", "sharedmember"], intersection=["sharedmember"]),
        ]
        for data in figures:
            with self.subTest(kind=data["kind"]):
                fig = figure_adapter.validate_python(data)
                visible = set(figure_texts(fig)) - {"0", "1", "2", "3"}
                report = check([item(figure=fig)])
                found = {w for e in report.errors if e.code == "out_of_scope" for w in e.words}
                self.assertEqual(found, visible)
                self.assertNotIn("internal", found)


class CLITests(unittest.TestCase):
    def run_cli(self, folder, question=None):
        folder = Path(folder)
        (folder / "lessons").mkdir(exist_ok=True)
        (folder / "batches").mkdir(exist_ok=True)
        (folder / "blueprint.json").write_text(blueprint().model_dump_json(), encoding="utf-8")
        (folder / "lessons/lesson.json").write_text(lesson().model_dump_json(), encoding="utf-8")
        (folder / "batches/batch.json").write_text(batch([question or item()]).model_dump_json(), encoding="utf-8")
        (folder / "moe2000.csv").write_text("headword,pos,basic_1200,note\nthe,,true,手寫\n", encoding="utf-8")
        (folder / "moe2000.meta.yaml").write_text(
            "source_url: https://example.invalid\nversion: fake\nretrieved_date: 2026-10-16\nusage_terms: 手寫假資料\nentry_count: 1\n", encoding="utf-8")
        args = ["check", str(folder / "blueprint.json"), str(folder / "batches"),
                "--lessons", str(folder / "lessons"), "--wordlist", str(folder / "moe2000.csv")]
        return args

    def test_exit_code_report_and_chinese_output(self):
        with tempfile.TemporaryDirectory() as folder:
            for question, expected in [(item(), 0), (item(stem="quokka"), 1)]:
                args = self.run_cli(folder, question)
                report_path = Path(folder) / "report.json"
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    code = main([*args, "--json", str(report_path)])
                self.assertEqual(code, expected)
                report = json.loads(report_path.read_text(encoding="utf-8"))
                self.assertEqual(bool(report["errors"]), bool(expected))
                self.assertIn("待審", output.getvalue())
                if expected:
                    self.assertIn("第 1 題", output.getvalue())
                    self.assertIn("quokka", output.getvalue())

    def test_malformed_input_becomes_error_report(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.run_cli(folder)
            (Path(folder) / "batches/batch.json").write_text("{bad", encoding="utf-8")
            report = Path(folder) / "report.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, "--json", str(report)]), 1)
            self.assertEqual(json.loads(report.read_text(encoding="utf-8"))["errors"][0]["code"], "input_error")

    def test_output_cannot_overwrite_inputs(self):
        with tempfile.TemporaryDirectory() as folder:
            args = self.run_cli(folder)
            bp = Path(folder) / "blueprint.json"
            before = bp.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([*args, "--json", str(bp)]), 1)
            self.assertEqual(bp.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
