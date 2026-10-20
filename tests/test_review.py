"""Human review page backend: drafts, final reviews bound to content, and the local HTTP API."""

import contextlib
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request
from functools import partial
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from dili import pipeline  # noqa: E402
from dili.check import Report  # noqa: E402
from dili.cli import main  # noqa: E402
from dili.review import attach_human, exam_payload, findings_for, store  # noqa: E402
from dili.review.server import Handler  # noqa: E402
from test_pipeline import BLUEPRINT, words_item  # noqa: E402


class ReviewTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._cwd = os.getcwd()
        os.chdir(self._tmp.name)
        self.catalog = pipeline.Catalog.load(ROOT / "data/lessons", ROOT / "data/wordlists/moe2000.csv")
        self.run = pipeline.plan_run("r1", BLUEPRINT, self.catalog)
        vocab = {v.id: v for lesson in self.catalog.lessons for v in lesson.vocab}
        ids = self.run.plan()["words"]["required"]["vocab"]
        items = [words_item(46 + n, i, vocab[i].headword) for n, i in enumerate(ids)]
        self.run.batch_path("words").write_text(json.dumps(
            {"run_id": "r1", "section_id": "words", "produced_by": "claude", "items": items}), encoding="utf-8")
        self.batch = self.run.load_batch("words")

    def tearDown(self):
        os.chdir(self._cwd)
        self._tmp.cleanup()

    def entries(self, **comments):
        return {i.id: {"content_hash": i.content_hash, "difficulty": "medium", "comment": comments.get(i.id, "")}
                for i in self.batch.items[:2]}

    def test_draft_is_not_final_and_blank_entries_are_dropped(self):
        data = self.entries()
        data["w99"] = {"content_hash": "x", "difficulty": None, "comment": "  "}
        store.save(self.run, "Richard", data, final=False)
        self.assertEqual(store.load(self.run, "Richard")["status"], "draft")
        self.assertNotIn("w99", store.load(self.run, "Richard")["entries"])
        self.assertEqual(store.finals(self.run), [])
        with self.assertRaises(ValueError):
            store.save(self.run, "Richard", {"w46": {"difficulty": "very hard"}}, final=False)
        with self.assertRaises(ValueError):
            store.load(self.run, "../escape")

    def test_final_review_approves_uncommented_items_of_the_same_content(self):
        store.save(self.run, "Richard", self.entries(w47="答案不唯一"), final=True)
        reviewed = attach_human(self.batch, store.finals(self.run))
        first, second = reviewed.items[:2]
        self.assertEqual(first.status, "reviewed")
        self.assertEqual(second.status, "pending")
        self.assertEqual(second.reviews[-1].decision, "request_changes")
        self.assertTrue(all(i.status == "pending" for i in reviewed.items[2:]))

        changed = first.model_copy(update={"stem": "A different sentence for you: b______."})
        self.assertEqual(changed.status, "pending")  # approval was for the old content

    def test_comments_on_older_versions_are_not_resent(self):
        store.save(self.run, "Richard", self.entries(w46="太簡單", w47="答案不唯一"), final=True)
        hashes = {i.id: i.content_hash for i in self.batch.items}
        self.assertEqual({f.object_id for f in findings_for(self.run, hashes)}, {"w46", "w47"})
        hashes["w47"] = "0" * 64
        found = findings_for(self.run, hashes)
        self.assertEqual([(f.object_id, f.notes) for f in found], [("w46", "太簡單")])
        self.assertIn("普通", found[0].severity)

    def test_every_save_preserves_history_even_with_identical_timestamps(self):
        fixed = datetime(2026, 10, 18, 12, 0, tzinfo=timezone.utc)
        saved = []
        with patch.object(store, "datetime") as clock:
            clock.now.return_value = fixed
            for final, comment in [(False, "第一次"), (False, "第二次"), (True, "定稿"), (False, "再閱卷")]:
                saved.append(store.save(self.run, "Richard", self.entries(w46=comment), final=final))
        history = sorted((self.run.root / "feedback/Richard/history").glob("*.json"))
        self.assertEqual(len(history), 4)
        self.assertCountEqual([json.loads(p.read_text(encoding="utf-8")) for p in history], saved)
        for path in history:
            self.assertRegex(path.name, r"^\d{8}T\d{6}\.\d{6}Z-(draft|final)\.json$")
        self.assertEqual(store.load(self.run, "Richard"), saved[-1])
        self.assertEqual(store.finals(self.run), [])  # history is never silently resubmitted

    def test_first_save_preserves_legacy_latest_exactly(self):
        latest = self.run.root / "feedback/Richard.json"
        latest.parent.mkdir(exist_ok=True)
        previous = {"reviewer": "Richard", "status": "final", "updated": "2026-10-17T12:00:00+00:00",
                    "entries": self.entries(w46="舊版意見")}
        raw = json.dumps(previous, ensure_ascii=False).encode("utf-8")
        latest.write_bytes(raw)
        saved = store.save(self.run, "Richard", self.entries(w46="新版意見"), final=True)
        history = list((latest.parent / "Richard/history").glob("*.json"))
        self.assertEqual(len(history), 2)
        self.assertIn(raw, [p.read_bytes() for p in history])
        self.assertEqual(store.load(self.run, "Richard"), saved)
        self.assertEqual(store.finals(self.run), [saved])

    def test_save_failures_leave_latest_intact(self):
        first = store.save(self.run, "Richard", self.entries(w46="保留"), final=True)
        with patch.object(store, "_archive", side_effect=OSError("history unavailable")):
            with self.assertRaises(OSError):
                store.save(self.run, "Richard", self.entries(w46="新意見"), final=False)
        self.assertEqual(store.load(self.run, "Richard"), first)
        with patch.object(store.os, "replace", side_effect=OSError("latest unavailable")):
            with self.assertRaises(OSError):
                store.save(self.run, "Richard", self.entries(w46="新意見"), final=False)
        self.assertEqual(store.load(self.run, "Richard"), first)
        self.assertEqual(len(list((self.run.root / "feedback/Richard/history").glob("*.json"))), 2)
        self.assertFalse(list((self.run.root / "feedback").glob("*.tmp")))

    def test_history_reviewer_directory_cannot_escape_or_alias(self):
        for reviewer in [".", "..", "../escape", "Richard.", "Richard ", " "]:
            with self.subTest(reviewer=reviewer), self.assertRaises(ValueError):
                store.save(self.run, reviewer, self.entries(), final=False)

    def test_findings_filter_keeps_content_hash_and_final_requirements(self):
        store.save(self.run, "Richard", self.entries(w46="第一位"), final=True)
        store.save(self.run, "Richard-1", self.entries(w47="第二位"), final=True)
        store.save(self.run, "Draft", self.entries(w46="草稿"), final=False)
        hashes = {i.id: i.content_hash for i in self.batch.items}
        self.assertEqual([f.notes for f in findings_for(self.run, hashes, reviewers=["Richard-1"])], ["第二位"])
        self.assertEqual(len(findings_for(self.run, hashes)), 2)
        self.assertEqual(len(findings_for(self.run, hashes, reviewers=["Richard", "Richard", "Richard-1"])), 2)
        self.assertEqual(findings_for(self.run, hashes, reviewers=["missing", "Draft"]), [])
        hashes["w47"] = "old"
        self.assertEqual(findings_for(self.run, hashes, reviewers=["Richard-1"]), [])

    def test_feedback_cli_repeated_reviewers_and_default(self):
        store.save(self.run, "Richard", self.entries(w46="第一位"), final=True)
        store.save(self.run, "Richard-1", self.entries(w47="第二位"), final=True)
        report = Report()
        report.add("errors", "out_of_scope", "程式錯誤", obj=self.batch.items[0], words=["quokka"])
        for names, expected in [(None, {"第一位", "第二位"}), (["Richard-1"], {"第二位"}),
                                (["Richard", "Richard-1", "Richard"], {"第一位", "第二位"}),
                                (["missing"], set())]:
            with self.subTest(names=names):
                args = ["feedback", "r1", "--section", "words"]
                for name in names or []:
                    args.extend(["--reviewer", name])
                # Never dispatch Agora or modify a real run from this CLI test.
                with patch.object(pipeline.Catalog, "load", return_value=self.catalog), \
                        patch.object(pipeline, "validate_section", return_value=report), \
                        patch.object(pipeline, "revise_section", return_value=True) as revise, \
                        contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(main(args), 0)
                revise.assert_called_once()
                found = revise.call_args.kwargs["findings"]
                self.assertEqual({f.notes for f in found if f.severity != "程式檢核"}, expected)
                self.assertEqual([f.notes for f in found if f.severity == "程式檢核"], ["程式錯誤（quokka）"])

    def test_http_api_round_trip(self):
        for section in self.run.blueprint().sections:  # payload needs every section
            if section.id != "words":
                self.run.batch_path(section.id).write_text(self.run.batch_path("words").read_text(encoding="utf-8"),
                                                           encoding="utf-8")
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, run=self.run, reviewer="Richard"))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            def post(path, body):
                request = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"),
                                                 headers={"Content-Type": "application/json"})
                return json.loads(urllib.request.urlopen(request).read())

            saved = post("/api/feedback", {"reviewer": "Richard", "entries": self.entries()})
            self.assertEqual((saved["status"], saved["count"]), ("draft", 2))
            loaded = json.loads(urllib.request.urlopen(base + "/api/feedback?reviewer=Richard").read())
            self.assertEqual(set(loaded["entries"]), {"w46", "w47"})
            self.assertEqual(post("/api/finish", {"reviewer": "Richard", "entries": self.entries()})["status"], "final")
        finally:
            server.shutdown()
            server.server_close()

    def test_payload_numbers_parts_and_hashes(self):
        for section in self.run.blueprint().sections:
            if section.id != "words":
                self.run.batch_path(section.id).write_text(self.run.batch_path("words").read_text(encoding="utf-8"),
                                                           encoding="utf-8")
        payload = exam_payload(self.run)
        words = next(p for p in payload["parts"] if p["title"] == "文意字彙")
        self.assertEqual((words["first"], words["last"], words["points"]), (46, 55, "每題 1 分，共 10 分"))
        self.assertEqual(words["entries"][0]["content_hash"], self.batch.items[0].content_hash)


if __name__ == "__main__":
    unittest.main()
