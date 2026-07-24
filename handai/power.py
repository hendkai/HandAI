"""Safe system power actions for the handheld GUI."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BatteryState:
    percent: int | None
    status: str
    source: str = ""

    @property
    def charging(self) -> bool:
        return self.status.casefold() in ("charging", "full")


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace").strip()
    except OSError:
        return ""


def battery_state(root: Path = Path("/sys/class/power_supply")) -> BatteryState:
    """Read the kernel power-supply class without assuming a vendor name."""
    try:
        supplies = sorted(item for item in root.iterdir() if item.is_dir())
    except OSError:
        return BatteryState(None, "Unavailable")
    batteries = [item for item in supplies
                 if _read(item / "type").casefold() == "battery"]
    candidates = batteries or [item for item in supplies
                               if (item / "capacity").exists()]
    for item in candidates:
        raw = _read(item / "capacity")
        try:
            percent = max(0, min(100, round(float(raw))))
        except ValueError:
            continue
        return BatteryState(
            percent,
            _read(item / "status") or "Unknown",
            item.name,
        )
    return BatteryState(None, "Unavailable")


def battery_label(state: BatteryState | None = None) -> str:
    state = state or battery_state()
    if state.percent is None:
        return "BATTERY: UNKNOWN"
    status = state.status.casefold()
    verb = ("CHARGING" if status == "charging" else
            "FULL" if status == "full" else
            "DISCHARGING" if status == "discharging" else
            state.status.upper())
    return f"BATTERY: {state.percent}% {verb}"


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
