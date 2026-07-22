"""Compose bridge - type into a running agent with only the gamepad.

Once you're inside an agent's TUI (in tmux), its prompt expects a keyboard. This
bridge lets a gamepad button pop up the HandAI on-screen keyboard (via a tmux
`display-popup` bound to a key), compose text, and inject it into the agent's
input with `tmux send-keys`. So the whole loop - navigate *and* type - is
button-only.

The tmux binding lives in etc/handai/tmux.conf; the popup runs
`python -m handai compose --target <session>`, which calls run_compose here.
"""

from __future__ import annotations

import subprocess


def send_keys_argv(target: str, text: str) -> list[str]:
    """argv that injects literal text into the target pane (no key-name parsing,
    `--` guards text starting with '-')."""
    return ["tmux", "send-keys", "-t", target, "-l", "--", text]


def enter_argv(target: str) -> list[str]:
    return ["tmux", "send-keys", "-t", target, "Enter"]


def send_text(target: str, text: str, enter: bool = True, timeout: float = 5.0) -> tuple[bool, str]:
    try:
        r = subprocess.run(send_keys_argv(target, text), capture_output=True,
                           text=True, timeout=timeout)
        if r.returncode != 0:
            return False, (r.stderr.strip() or "send-keys failed")
        if enter:
            r2 = subprocess.run(enter_argv(target), capture_output=True,
                               text=True, timeout=timeout)
            if r2.returncode != 0:
                return False, (r2.stderr.strip() or "send Enter failed")
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"send failed: {e}"
    return True, "sent"


def run_compose(target: str, enter: bool = True) -> int:
    """Interactive: OSK -> send into target. Meant to run inside a tmux popup."""
    import curses

    from .osk import prompt

    def _ui(stdscr):
        return prompt(stdscr, f"Compose -> {target}"
                              + ("  (sends Enter)" if enter else "  (no Enter)"))

    text = curses.wrapper(_ui)
    if not text:  # cancelled or empty -> nothing to send
        return 0
    ok, msg = send_text(target, text, enter=enter)
    if not ok:
        print(f"[handai compose] {msg}")
        return 1
    return 0
