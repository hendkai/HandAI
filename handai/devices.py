"""Persistent remote targets managed from the cockpit."""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

_HOST = re.compile(r"^(?:[A-Za-z0-9_.-]+@)?(?:[A-Za-z0-9_.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?$")


def state_dir() -> Path:
    return Path(os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai"))


def registry_path() -> Path:
    return state_dir() / "devices.json"


def slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return value or "remote"


def validate_ssh_host(host: str) -> str:
    host = host.strip()
    if not _HOST.fullmatch(host):
        raise ValueError("host must look like user@hostname or user@host:port")
    if host.rsplit(":", 1)[-1].isdigit() and ":" in host:
        port = int(host.rsplit(":", 1)[-1])
        if not 1 <= port <= 65535:
            raise ValueError("SSH port must be between 1 and 65535")
    return host


def validate_gateway_url(url: str) -> str:
    url = url.strip()
    if not re.fullmatch(r"wss?://[^\s/]+(?::[0-9]{1,5})?(?:/[^\s]*)?", url):
        raise ValueError("gateway URL must start with ws:// or wss://")
    # Public endpoints must be encrypted; private/Tailscale/mDNS may use ws.
    if url.startswith("ws://"):
        host = url[5:].split("/", 1)[0].split(":", 1)[0].lower()
        private = (host in ("localhost", "127.0.0.1") or host.endswith((".local", ".ts.net"))
                   or host.startswith(("10.", "192.168.", "100."))
                   or (host.startswith("172.") and host.split(".")[1].isdigit()
                       and 16 <= int(host.split(".")[1]) <= 31))
        if not private:
            raise ValueError("public OpenClaw gateways require wss://")
    return url


def validate_hermes_url(url: str) -> str:
    url=url.strip().rstrip("/")
    if not re.fullmatch(r"https?://[^\s/]+(?::[0-9]{1,5})?(?:/[^\s]*)?",url):
        raise ValueError("Hermes URL must start with http:// or https://")
    if url.startswith("http://"):
        host=url[7:].split("/",1)[0].split(":",1)[0].lower()
        private=(host in ("localhost","127.0.0.1") or host.endswith((".local",".ts.net"))
                 or host.startswith(("10.","192.168.","100."))
                 or (host.startswith("172.") and host.split(".")[1].isdigit()
                     and 16<=int(host.split(".")[1])<=31))
        if not private:raise ValueError("public Hermes servers require https://")
    return url


@dataclass(frozen=True)
class RemoteDevice:
    id: str
    label: str
    kind: str  # ssh | openclaw-gateway | hermes-api
    address: str
    default_workdir: str = "~/projects"


def load(path: Path | None = None) -> list[RemoteDevice]:
    target = path or registry_path()
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text("utf-8"))
        return [RemoteDevice(**item) for item in raw if item.get("kind") in ("ssh", "openclaw-gateway", "hermes-api")]
    except (OSError, ValueError, TypeError):
        return []


def save(items: list[RemoteDevice], path: Path | None = None) -> None:
    target = path or registry_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps([asdict(item) for item in items], indent=2) + "\n", "utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    tmp.replace(target)


def upsert(item: RemoteDevice, path: Path | None = None) -> list[RemoteDevice]:
    items = [old for old in load(path) if old.id != item.id]
    items.append(item)
    save(items, path)
    return items


def remove(device_id: str, path: Path | None = None) -> list[RemoteDevice]:
    items = [item for item in load(path) if item.id != device_id]
    save(items, path)
    return items
