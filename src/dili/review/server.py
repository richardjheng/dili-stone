"""Local review page: http://127.0.0.1:<port>/ shows the exam; drafts and final reviews are saved into the run."""

import json
import webbrowser
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from ..pipeline.runs import Run
from . import store
from .payload import exam_payload

PAGE = Path(__file__).with_name("static") / "index.html"


class Handler(BaseHTTPRequestHandler):
    def __init__(self, *args, run: Run, reviewer: str, **kwargs):
        self.run, self.reviewer = run, reviewer
        super().__init__(*args, **kwargs)

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    def _send(self, status: int, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, data, status: int = 200) -> None:
        self._send(status, json.dumps(data, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def do_GET(self):
        url = urlparse(self.path)
        try:
            if url.path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif url.path == "/api/exam":
                self._json({**exam_payload(self.run), "reviewer": self.reviewer})
            elif url.path == "/api/feedback":
                reviewer = parse_qs(url.query).get("reviewer", [self.reviewer])[0]
                self._json(store.load(self.run, reviewer))
            else:
                self._json({"error": "not found"}, 404)
        except ValueError as exc:
            self._json({"error": str(exc)}, 400)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path not in ("/api/feedback", "/api/finish"):
            self._json({"error": "not found"}, 404)
            return
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))).decode("utf-8"))
            saved = store.save(self.run, body.get("reviewer") or self.reviewer, body.get("entries") or {},
                               final=url.path == "/api/finish")
            self._json({"ok": True, "status": saved["status"], "updated": saved["updated"],
                        "count": len(saved["entries"])})
        except (ValueError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)


def serve(run: Run, *, reviewer: str, port: int = 8765, open_browser: bool = True) -> None:
    url = f"http://127.0.0.1:{port}/?reviewer={quote(reviewer)}"
    try:
        server = ThreadingHTTPServer(("127.0.0.1", port), partial(Handler, run=run, reviewer=reviewer))
    except OSError:
        # Already running (e.g. started twice): just bring the page up again.
        print(f"閱卷網頁已在執行：{url}")
        if open_browser:
            webbrowser.open(url)
        return
    print(f"閱卷網頁：{url}（閱卷人：{reviewer}）；按 Ctrl+C 結束。")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
