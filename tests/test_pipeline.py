"""M4 pipeline with Agora replaced by a fake worker that writes hand-written batches."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from dili import pipeline  # noqa: E402
from dili.pipeline import agora, steps  # noqa: E402
from dili.pipeline.workorder import generation_order  # noqa: E402

BLUEPRINT = ROOT / "blueprints" / "hanlin-b3-term2.json"


def words_item(number: int, vocab_id: str, answer: str, **extra) -> dict:
    return {"id": f"w{number}", "section_id": "words", "kind": "word_in_context", "number": number,
            "stem": f"Sentence number {number} has a word for you: {answer[0]}______.",
            "prompt": {"task": "word_in_context", "instruction": "依句意及字首填入適當的單字",
                       "first_letter": answer[0]},
            "answer": {"accepted_text": [answer]},
            "explanation": {"correct_zh": f"依句意填入 {answer}。"},
            "targets": {"vocab_ids": [vocab_id]}, **extra}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.catalog = pipeline.Catalog.load(ROOT / "data/lessons", ROOT / "data/wordlists/moe2000.csv")
        self.run = pipeline.plan_run("t1", BLUEPRINT, self.catalog)
        self.vocab = {v.id: v for lesson in self.catalog.lessons for v in lesson.vocab}

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def words_batch(self, *, bad: bool = False) -> dict:
        ids = self.run.plan()["words"]["required"]["vocab"]
        items = [words_item(46 + n, i, self.vocab[i].headword) for n, i in enumerate(ids)]
        if bad:
            items[0]["stem"] = "The quokka likes the new teacher. (q______)"
        return {"run_id": "t1", "section_id": "words", "produced_by": "codex", "items": items}

    def test_plan_spreads_targets(self):
        plan = self.run.plan()
        words = plan["words"]["required"]["vocab"]
        self.assertEqual(len(words), 10)
        self.assertEqual(len({w.split("-")[0] for w in words}), 4)  # every unit
        self.assertTrue(all(self.vocab[w].textbook_category == "application" for w in words))
        self.assertFalse(set(words) & set(plan["choice"]["required"]["vocab"]))
        grammar = [g.id for lesson in self.catalog.lessons for g in lesson.grammar]
        self.assertEqual(plan["choice"]["required"]["grammar"], grammar)
        self.assertEqual(len(plan["writing"]["required"]["grammar"]), 5)
        self.assertNotEqual(plan["words"]["worker"], plan["words"]["reviewer"])
        self.assertEqual(Path("runs/CURRENT").read_text(encoding="utf-8").strip(), "t1")

    def test_work_order_states_numbers_targets_and_output(self):
        order = generation_order(self.run, "cloze", self.catalog.scope(self.run.blueprint()), worker="claude")
        self.assertIn("題號 **26～35**", order)
        self.assertIn("runs/t1/batches/cloze.json", order)
        self.assertIn("u4-v4-lawyer", order)
        self.assertIn("agora build", order)

    def test_work_order_wordlist_rules_follow_blueprint(self):
        from unittest.mock import patch
        original = self.run.blueprint()
        for basic, extended, phrase in [(False, False, "課綱 1200 基本字與 800 字均未開放"),
                                        (True, False, "課綱 800 字（本卷未開放）"),
                                        (True, True, "課綱 **800 字**（`basic_1200=false`）")]:
            with self.subTest(basic=basic, extended=extended):
                bp = original.model_copy(update={"prerequisite": original.prerequisite.model_copy(update={
                    "allow_basic_1200": basic, "allow_extended_800": extended})})
                with patch.object(type(self.run), "blueprint", return_value=bp):
                    order = generation_order(self.run, "cloze", self.catalog.scope(bp), worker="claude")
                self.assertIn(phrase, order)
                self.assertEqual("課綱 **1200 基本字**" in order, basic)
                self.assertIn("prerequisite.lesson_ids", order)
                self.assertIn("單字考點題的選項仍須完整對應考試範圍內", order)
                self.assertIn("其他字都要", order)
                self.assertNotIn("{vocabulary_rule}", order)

    def test_generate_retries_with_errors_then_accepts(self):
        deliveries = [self.words_batch(bad=True), self.words_batch()]
        orders = []

        def fake_assign(thread, worker, order):
            orders.append(order.read_text(encoding="utf-8"))
            self.run.batch_path("words").write_text(json.dumps(deliveries[len(orders) - 1]), encoding="utf-8")

        patches = {"new_thread": lambda topic: "thread-1", "assign": fake_assign, "wait": lambda t: "done",
                   "is_active": lambda t: False}
        originals = {k: getattr(agora, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(agora, k, v)
            self.assertTrue(steps.generate_section(self.run, "words", self.catalog, log=lambda *_: None))
        finally:
            for k, v in originals.items():
                setattr(agora, k, v)
        self.assertEqual(len(orders), 2)
        self.assertNotIn("上一次交付的檢核錯誤", orders[0])
        self.assertIn("quokka", orders[1])
        state = self.run.state()["sections"]["words"]
        self.assertEqual((state["status"], state["attempts"], state["thread"]), ("generated", 2, "thread-1"))

    def test_review_notes_never_approve_and_majors_surface(self):
        self.run.batch_path("words").write_text(json.dumps(self.words_batch()), encoding="utf-8")
        batch = self.run.load_batch("words")
        review = steps.ReviewFile.model_validate({"reviewer": "antigravity", "findings": [
            {"object_id": "w46", "severity": "major", "notes": "答案不唯一，建議改句子。"},
            {"object_id": "w47", "severity": "ok", "notes": "答案與解析正確。"}]})
        reviewed = steps.attach_reviews(batch, review)
        self.assertTrue(all(i.status == "pending" for i in reviewed.items))
        self.assertEqual(reviewed.items[0].reviews[0].role, "ai")
        flags = steps.ai_review_flags([reviewed])
        self.assertEqual([w.item_id for w in flags.warnings], ["w46"])

    def test_revise_returns_findings_to_writer_and_retires_old_review(self):
        self.run.batch_path("words").write_text(json.dumps(self.words_batch()), encoding="utf-8")
        self.run.update_section("words", thread="thread-1")
        self.run.review_path("words").write_text(json.dumps({"reviewer": "antigravity", "findings": [
            {"object_id": "w46", "severity": "major", "notes": "答案不唯一。"},
            {"object_id": "w47", "severity": "ok", "notes": "沒問題。"}]}), encoding="utf-8")
        orders = []
        patches = {"assign": lambda t, w, o: orders.append(o.read_text(encoding="utf-8")),
                   "wait": lambda t: "done", "is_active": lambda t: False}
        originals = {k: getattr(agora, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(agora, k, v)
            self.assertTrue(steps.revise_section(self.run, "words", self.catalog, log=lambda *_: None))
        finally:
            for k, v in originals.items():
                setattr(agora, k, v)
        self.assertIn("`w46`（major）：答案不唯一。", orders[0])
        self.assertNotIn("w47", orders[0])
        self.assertFalse(self.run.review_path("words").exists())
        self.assertEqual(self.run.state()["sections"]["words"]["review_status"], "revised")

    def test_reviewer_must_differ_from_writer(self):
        self.run.batch_path("words").write_text(json.dumps(self.words_batch()), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "出題以外"):
            steps.review_section(self.run, "words", self.catalog, reviewer="codex")

    def test_build_refuses_incomplete_exam(self):
        self.run.batch_path("words").write_text(json.dumps(self.words_batch()), encoding="utf-8")
        self.assertFalse(steps.build_run(self.run, self.catalog, log=lambda *_: None))
        report = json.loads((self.run.root / "report.json").read_text(encoding="utf-8"))
        self.assertIn("section_batch_count", {e["code"] for e in report["errors"]})
        self.assertFalse(list((self.run.root / "out").iterdir()))


if __name__ == "__main__":
    unittest.main()


class MakeTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.catalog = pipeline.Catalog.load(ROOT / "data/lessons", ROOT / "data/wordlists/moe2000.csv")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def test_failed_writer_falls_back_and_reviewer_differs(self):
        from dili.pipeline import make

        calls = {"generate": [], "review": [], "revise": [], "build": 0}

        def generate(run, section, catalog, *, worker, log):
            calls["generate"].append((section, worker))
            ok = not (section == "words" and worker == "codex")  # codex fails once
            run.update_section(section, worker=worker, status="generated" if ok else "failed")
            return ok

        def review(run, section, catalog, *, reviewer, log):
            calls["review"].append((section, reviewer))
            run.update_section(section, review_status="reviewed")
            return True

        def revise(run, section, catalog, *, worker, log):
            calls["revise"].append((section, worker))
            return True

        def build(run, catalog, *, log, write_word):
            calls["build"] += 1
            self.assertFalse(write_word)
            return True

        patches = {"generate_section": generate, "review_section": review, "revise_section": revise,
                   "build_run": build, "STAGGER": 0}
        originals = {k: getattr(make, k) for k in patches}
        try:
            for k, v in patches.items():
                setattr(make, k, v)
            run = make.make_exam("m1", BLUEPRINT, self.catalog, log=lambda *_: None)
        finally:
            for k, v in originals.items():
                setattr(make, k, v)
        self.assertIn(("words", "codex"), calls["generate"])
        self.assertIn(("words", "claude"), calls["generate"])  # next after codex in FALLBACK
        state = run.state()["sections"]
        self.assertTrue(all(e["reviewer"] != e["worker"] for e in state.values()))
        self.assertEqual(len(calls["review"]), 5)
        self.assertEqual(len(calls["revise"]), 5)
        self.assertEqual(calls["build"], 1)
        self.assertEqual(Path("runs/CURRENT").read_text(encoding="utf-8").strip(), "m1")
