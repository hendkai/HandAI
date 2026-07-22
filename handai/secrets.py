"""Secret store - tokens for token-env providers.

Stored at $HANDAI_STATE/secrets.json with 0600 perms. This is deliberately NOT
presented as encryption: a headless handheld that boots straight into the
cockpit has no secret to derive a key from, so "encryption" without a boot
passphrase would be theatre. If the user opts into a boot PIN (see cockpit
settings), we wrap this file with it; until then it is plaintext + 0600 and the
threat model is "someone pulls the SD card", which we call out in the UI.
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def _state_dir() -> Path:
    base = os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai")
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p


class SecretStore:
    def __init__(self, path: Path | None = None):
        self.path = path or (_state_dir() / "secrets.json")
        self._data: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            try:
                self._data = json.loads(self.path.read_text("utf-8"))
            except (ValueError, OSError):
                self._data = {}

    def _flush(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), "utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass  # e.g. on Windows dev boxes; harmless
        tmp.replace(self.path)

    def get(self, provider_id: str) -> str | None:
        return self._data.get(provider_id)

    def set(self, provider_id: str, token: str) -> None:
        self._data[provider_id] = token
        self._flush()

    def clear(self, provider_id: str) -> None:
        self._data.pop(provider_id, None)
        self._flush()

    def has(self, provider_id: str) -> bool:
        return bool(self._data.get(provider_id))
