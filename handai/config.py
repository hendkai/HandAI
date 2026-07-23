"""Config loading - providers, modes, and recent workdirs from JSON.

JSON (not YAML/TOML) on purpose: zero third-party deps so the cockpit runs on a
bare Python in a minimal rootfs. Environment variables in string values are
expanded so deployment-specific remote targets never need to be hard-coded.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .providers import Mode, Provider, parse_modes, parse_providers
from . import devices


def _expand(obj):
    if isinstance(obj, str):
        return os.path.expandvars(obj)
    if isinstance(obj, list):
        return [_expand(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _expand(v) for k, v in obj.items()}
    return obj


_UNRESOLVED_ENV = re.compile(r"\$(?:\{[A-Za-z_][A-Za-z0-9_]*\}|[A-Za-z_][A-Za-z0-9_]*)")


def _mode_is_configured(mode: Mode) -> bool:
    """Hide remote presets whose environment-backed target was never set.

    ``os.path.expandvars`` deliberately leaves an unknown variable untouched.
    Passing that literal value to ssh would expose a dead entry in the device
    menu and produce a confusing connection failure.
    """
    target = mode.host or mode.endpoint or ""
    return mode.transport == "local" or not _UNRESOLVED_ENV.search(target)


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
        static_ssh = any(m.transport == "ssh" and not m.id.startswith("managed-")
                         and provider.allows_mode(m.id) for m in self.modes)
        return [m for m in self.modes if provider.allows_mode(m.id)
                or (m.transport == "ssh" and m.id.startswith("managed-") and static_ssh)
                or (m.transport == "openclaw-gateway" and provider.id == "openclaw")
                or (m.transport == "hermes-api" and provider.id == "hermes")]

    def reload_devices(self, path: Path | None = None) -> None:
        self.modes = [m for m in self.modes if not m.id.startswith("managed-")]
        for item in devices.load(path):
            if item.kind == "ssh":
                self.modes.append(Mode("managed-" + item.id, item.label, "ssh",
                                       host=item.address, default_workdir=item.default_workdir))
            elif item.kind == "openclaw-gateway":
                self.modes.append(Mode("managed-" + item.id, item.label,
                                       "openclaw-gateway", endpoint=item.address,
                                       default_workdir=item.default_workdir))
            else:
                self.modes.append(Mode("managed-" + item.id, item.label,
                                       "hermes-api", endpoint=item.address,
                                       default_workdir=item.default_workdir))

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_path()
        raw = _expand(json.loads(Path(path).read_text("utf-8")))
        modes = parse_modes(raw.get("modes", []))
        cfg = cls(
            providers=parse_providers(raw.get("providers", [])),
            modes=[mode for mode in modes if _mode_is_configured(mode)],
            recent=list(raw.get("recent_workdirs", [])),
            skills=dict(raw.get("skills", {})),
        )
        cfg.reload_devices()
        return cfg
