"""Safe system power actions for the handheld GUI."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def capabilities(state_file: Path = Path("/sys/power/state")) -> dict[str, bool]:
    states = ""
    try:
        states = state_file.read_text("utf-8")
    except OSError:
        pass
    device = os.name == "posix"
    return {
        "shutdown": device and shutil.which("poweroff") is not None,
        "reboot": device and shutil.which("reboot") is not None,
        "suspend": device and "mem" in states.split(),
    }


def execute(action: str, state_file: Path = Path("/sys/power/state")) -> tuple[bool, str]:
    caps = capabilities(state_file)
    if action not in caps:
        return False, "unknown power action"
    if not caps[action]:
        return False, f"{action} is unavailable on this system"
    try:
        if hasattr(os, "sync"):
            os.sync()
        if action == "suspend":
            state_file.write_text("mem", "ascii")
        else:
            subprocess.Popen(["poweroff" if action == "shutdown" else "reboot"])
        return True, f"{action} requested"
    except OSError as exc:
        return False, str(exc)
