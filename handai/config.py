"""Config loading - providers, modes, and recent workdirs from JSON.

JSON (not YAML/TOML) on purpose: zero third-party deps so the cockpit runs on a
bare Python in a minimal rootfs. Env vars in string values are expanded, so the
example config can reference ${ANTHROPIC_API_KEY} etc. without hard-coding.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .providers import Mode, Provider, parse_modes, parse_providers


def _expand(obj):
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, list):
        return [_expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    return obj


def config_path() -> Path:
    env = os.environ.get("HANDAI_CONFIG")
    if env:
        return Path(env)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "handai" / "handai.json"


class Config:
    def __init__(self, providers: list[Provider], modes: list[Mode], recent: list[str],
                 skills: dict | None = None):
        self.providers = providers
        self.modes = modes
        self.recent_workdirs = recent
        self.skills = skills or {}

    @property
    def skills_dir(self) -> str | None:
        """Configured hub location (may be None -> skills.hub_dir default applies)."""
        return self.skills.get("dir")

    def provider(self, pid: str) -> Provider | None:
        return next((p for p in self.providers if p.id == pid), None)

    def mode(self, mid: str) -> Mode | None:
        return next((m for m in self.modes if m.id == mid), None)

    def modes_for(self, provider: Provider) -> list[Mode]:
        return [m for m in self.modes if provider.allows_mode(m.id)]

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        raw = _expand(json.loads(Path(path).read_text("utf-8")))
        return cls(
            providers=parse_providers(raw.get("providers", [])),
            modes=parse_modes(raw.get("modes", [])),
            recent=list(raw.get("recent_workdirs", [])),
            skills=dict(raw.get("skills", {})),
        )
