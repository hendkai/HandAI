"""tmux session inventory - local and per-remote-host.

The cockpit's session list comes from here. Local sessions live on the device's
tmux server; remote sessions live on each ssh host's own tmux server, which is
exactly why a remote agent keeps running after you detach the handheld.
"""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

from .providers import Mode
from .remote import ssh_argv
from .router import Target

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
            ssh_argv(host,f"tmux list-sessions -F '{_FMT}'",batch=True),
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired, ValueError):
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
                ssh_argv(session.host," ".join(cmd),batch=True),
                capture_output=True, text=True, timeout=timeout,
            )
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return False


def attach_argv(session: SessionInfo) -> list[str]:
    """argv that re-enters an existing session (used by the cockpit to exec)."""
    inner = ["tmux", "attach-session", "-t", session.name]
    if session.host:
        return ssh_argv(session.host," ".join(inner),tty=True)
    return inner


def start_target(target: Target, timeout: float = 12.0) -> tuple[bool, str]:
    try:
        result=subprocess.run(target.detached_argv,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired,ValueError) as exc:
        return False,str(exc)
    detail=(result.stderr or result.stdout).strip()
    return result.returncode==0,detail or ("SESSION READY" if result.returncode==0 else "SESSION START FAILED")


def from_target(target: Target) -> SessionInfo:
    return SessionInfo(target.session,1,False,target.mode.host if target.mode.is_ssh else None)


def capture(session: SessionInfo, lines: int = 14, timeout: float = 8.0) -> list[str]:
    count=max(1,min(100,int(lines)))
    command=["tmux","capture-pane","-p","-t",session.name,"-S",f"-{count}"]
    try:
        if session.host:
            remote_command=" ".join(shlex.quote(item) for item in command)
            result=subprocess.run(ssh_argv(session.host,remote_command,batch=True),
                                  capture_output=True,text=True,timeout=timeout)
        else:
            result=subprocess.run(command,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired,ValueError) as exc:
        return [f"SESSION READ FAILED: {exc}"]
    if result.returncode!=0:
        return [(result.stderr.strip() or "SESSION IS NOT AVAILABLE")]
    output=[line.rstrip() for line in result.stdout.splitlines()]
    while output and not output[-1]:output.pop()
    return output[-count:] or ["SESSION RUNNING - WAITING FOR OUTPUT"]


def send_text(session: SessionInfo, text: str, enter: bool = True,
              timeout: float = 8.0) -> tuple[bool, str]:
    value=str(text)
    if not value:return False,"PROMPT IS EMPTY"
    buffer="handai-input"
    load=["tmux","load-buffer","-b",buffer,"-"]
    paste=["tmux","paste-buffer","-b",buffer,"-d","-t",session.name]
    press=["tmux","send-keys","-t",session.name,"Enter"]
    try:
        if session.host:
            command=" ".join(shlex.quote(item) for item in load)+" && "+" ".join(shlex.quote(item) for item in paste)
            if enter:command+=" && "+" ".join(shlex.quote(item) for item in press)
            result=subprocess.run(ssh_argv(session.host,command,batch=True),input=value,
                                  capture_output=True,text=True,timeout=timeout)
        else:
            result=subprocess.run(load,input=value,capture_output=True,text=True,timeout=timeout)
            if result.returncode==0:
                result=subprocess.run(paste,capture_output=True,text=True,timeout=timeout)
            if enter and result.returncode==0:
                result=subprocess.run(press,capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired,ValueError) as exc:
        return False,str(exc)
    detail=(result.stderr or result.stdout).strip()
    return result.returncode==0,detail or ("PROMPT SENT" if result.returncode==0 else "PROMPT SEND FAILED")
