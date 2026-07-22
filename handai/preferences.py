"""Small persistent cockpit preferences and first-run state."""

from __future__ import annotations

import json
import os
from pathlib import Path


def path() -> Path:
    root = Path(os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai"))
    return root / "preferences.json"


def load(target: Path | None = None) -> dict:
    try:
        return json.loads((target or path()).read_text("utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def save(data: dict, target: Path | None = None) -> None:
    out = target or path()
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(out)


def completed(target: Path | None = None) -> bool:
    return bool(load(target).get("onboarding_complete"))


def mark_completed(target: Path | None = None) -> None:
    data = load(target)
    data["onboarding_complete"] = True
    save(data, target)


DEFAULT_BUTTONS = {0:"a", 1:"b", 2:"cancel", 6:"b", 7:"done",
                   11:"up", 12:"down", 13:"left", 14:"right"}


def button_map(target: Path | None = None) -> dict[int, str]:
    raw = load(target).get("gamepad", {})
    try:
        return {int(k): str(v) for k, v in raw.items()} or dict(DEFAULT_BUTTONS)
    except (TypeError, ValueError):
        return dict(DEFAULT_BUTTONS)


def save_button_map(mapping: dict[int, str], target: Path | None = None) -> None:
    data = load(target)
    data["gamepad"] = {str(k): v for k, v in mapping.items()}
    save(data, target)
