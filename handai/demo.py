"""Offline on-device demo session backed by a real persistent tmux pane."""

from __future__ import annotations

import shutil
import subprocess

SESSION = "handai-demo-local-offline"
AGENT = "/usr/share/handai/demo-agent.sh"


def available() -> bool:
    return shutil.which("tmux") is not None


def _run(argv: list[str], timeout: float = 4.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def running() -> bool:
    result = _run(["tmux", "has-session", "-t", SESSION])
    return bool(result and result.returncode == 0)


def start() -> tuple[bool, str]:
    if not available():
        return False, "TMUX IS NOT INSTALLED"
    if running():
        return True, "DEMO SESSION RESUMED"
    command = f"exec sh {AGENT}"
    result = _run(["tmux", "new-session", "-d", "-s", SESSION, command])
    if not result or result.returncode != 0:
        detail = (result.stderr.strip() if result else "") or "TMUX START FAILED"
        return False, detail
    return True, "OFFLINE DEMO SESSION STARTED"


def send(text: str) -> tuple[bool, str]:
    value = str(text).strip()
    if not value:
        return False, "PROMPT IS EMPTY"
    ok, detail = start()
    if not ok:
        return False, detail
    result = _run(["tmux", "send-keys", "-t", SESSION, "-l", value])
    if not result or result.returncode != 0:
        return False, (result.stderr.strip() if result else "") or "PROMPT SEND FAILED"
    result = _run(["tmux", "send-keys", "-t", SESSION, "Enter"])
    if not result or result.returncode != 0:
        return False, (result.stderr.strip() if result else "") or "ENTER SEND FAILED"
    return True, "PROMPT SENT"


def capture(lines: int = 14) -> list[str]:
    if not running():
        return ["OFFLINE DEMO IS NOT RUNNING"]
    result = _run(["tmux", "capture-pane", "-p", "-t", SESSION, "-S", f"-{max(1, lines)}"])
    if not result or result.returncode != 0:
        return ["COULD NOT READ DEMO SESSION"]
    output = [line.rstrip() for line in result.stdout.splitlines()]
    while output and not output[-1]:
        output.pop()
    return output[-lines:] or ["DEMO SESSION READY"]


def reset() -> bool:
    result = _run(["tmux", "kill-session", "-t", SESSION])
    return bool(result and result.returncode == 0)
