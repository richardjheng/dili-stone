"""Thin wrapper over the Agora CLI: open a thread, assign a work order, wait for the worker."""

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ACTIVE = {"starting", "running", "waiting"}


def _command() -> list[str]:
    script = os.environ.get("DILI_AGORA")
    if script:
        return [sys.executable, script]
    found = shutil.which("agora")
    return [found] if found else [sys.executable, "D:/github/agora/agora.py"]


def _run(*args: str) -> str:
    proc = subprocess.run([*_command(), *args], capture_output=True, text=True, encoding="utf-8",
                          errors="replace")
    if proc.returncode:
        raise RuntimeError(f"agora {' '.join(args[:1])} 失敗：{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout


def new_thread(topic: str) -> str:
    return _run("new", topic).strip().splitlines()[-1]


def assign(thread: str, worker: str, order: Path) -> None:
    _run("assign", thread, "--from", "claude", "--to", worker, "--file", str(order))


def thread_dir(thread: str, workspace: Path = Path(".")) -> Path:
    return workspace / ".agora" / "threads" / thread


def wait(thread: str, *, timeout: float = 3 * 3600, poll: float = 20) -> str:
    """Block until the assignment leaves the active states; returns the final relay status."""
    state_path = thread_dir(thread) / "state.json"
    deadline = time.monotonic() + timeout
    while True:
        relay = json.loads(state_path.read_text(encoding="utf-8")).get("relay") or {}
        status = relay.get("status", "unknown")
        if status not in ACTIVE:
            return status
        if time.monotonic() >= deadline:
            return "timeout"
        time.sleep(poll)


def worker_denials(thread: str) -> list[str]:
    """Tool calls the headless worker was refused (a refused source must not be trusted)."""
    log = thread_dir(thread) / "worker.log"
    if not log.exists():
        return []
    return [line.strip() for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
            if "denied_actions" in line]
