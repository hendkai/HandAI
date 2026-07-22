"""tmux session inventory - local and per-remote-host.

The cockpit's session list comes from here. Local sessions live on the device's
tmux server; remote sessions live on each ssh host's own tmux server, which is
exactly why a remote agent keeps running after you detach the handheld.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from .providers import Mode

_FMT = "#{session_name}\t#{session_windows}\t#{?session_attached,attached,detached}"


@dataclass(frozen=True)
class SessionInfo:
    name: str
    windows: int
    attached: bool
    host: str | None  # None = local device


def _parse(text: str, host: str | None) -> list[SessionInfo]:
    out: list[SessionInfo] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        name, windows, state = parts[0], parts[1], parts[2]
        if not name.startswith("handai-"):
            continue  # ignore unrelated tmux sessions
        out.append(
            SessionInfo(
                name=name,
                windows=int(windows) if windows.isdigit() else 1,
                attached=(state == "attached"),
                host=host,
            )
        )
    return out


def list_local(timeout: float = 3.0) -> list[SessionInfo]:
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", _FMT],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []  # "no server running" -> no sessions
    return _parse(r.stdout, host=None)


def list_remote(host: str, timeout: float = 6.0) -> list[SessionInfo]:
    try:
        r = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host,
             f"tmux list-sessions -F '{_FMT}'"],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if r.returncode != 0:
        return []
    return _parse(r.stdout, host=host)


def list_all(modes: list[Mode]) -> list[SessionInfo]:
    """Union of local sessions plus every distinct ssh host in the modes."""
    sessions = list_local()
    seen_hosts: set[str] = set()
    for m in modes:
        if m.is_remote and m.host and m.host not in seen_hosts:
            seen_hosts.add(m.host)
            sessions.extend(list_remote(m.host))
    return sessions


def kill(session: SessionInfo, timeout: float = 6.0) -> bool:
    cmd = ["tmux", "kill-session", "-t", session.name]
    try:
        if session.host:
            r = subprocess.run(
                ["ssh", session.host, " ".join(cmd)],
                capture_output=True, text=True, timeout=timeout,
            )
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def attach_argv(session: SessionInfo) -> list[str]:
    """argv that re-enters an existing session (used by the cockpit to exec)."""
    inner = ["tmux", "attach-session", "-t", session.name]
    if session.host:
        return ["ssh", "-t", session.host, " ".join(inner)]
    return inner
