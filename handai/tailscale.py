"""Small, testable wrapper around the Tailscale CLI."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass

_URL_RE = re.compile(r"https://login\.tailscale\.com/[A-Za-z0-9/?&=_-]+")


@dataclass(frozen=True)
class Status:
    available: bool
    state: str = "unavailable"
    ips: tuple[str,...] = ()
    name: str = ""

    @property
    def online(self) -> bool:
        return self.state.lower() == "running" and bool(self.ips)


def available() -> bool:
    return shutil.which("tailscale") is not None


def parse_status(text:str) -> Status:
    try:data=json.loads(text)
    except (ValueError,TypeError):return Status(True,"unknown")
    own=data.get("Self") or {}
    ips=data.get("TailscaleIPs") or own.get("TailscaleIPs") or []
    return Status(True,str(data.get("BackendState") or "unknown"),tuple(str(x) for x in ips),str(own.get("DNSName") or "").rstrip("."))


def status(timeout:float=4.0) -> Status:
    if not available():return Status(False)
    try:r=subprocess.run(["tailscale","status","--json"],capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired):return Status(True,"error")
    return parse_status(r.stdout) if r.stdout.strip() else Status(True,"stopped")


def parse_login_url(text:str) -> str|None:
    match=_URL_RE.search(text or "")
    return match.group(0) if match else None


def login_url(timeout:float=14.0) -> tuple[bool,str]:
    """Start interactive login briefly and return its phone-safe auth URL."""
    if not available():return False,"tailscale CLI unavailable"
    try:
        r=subprocess.run(["tailscale","login","--timeout=10s"],capture_output=True,text=True,timeout=timeout)
        output=(r.stdout or "")+"\n"+(r.stderr or "")
    except subprocess.TimeoutExpired as e:
        def decoded(value):return value.decode("utf-8","replace") if isinstance(value,bytes) else (value or "")
        output=decoded(e.stdout)+decoded(e.stderr)
    except OSError as e:return False,f"tailscale login failed: {e}"
    url=parse_login_url(output)
    if url:return True,url
    if status().online:return True,"already-online"
    return False,(output.strip()[-240:] or "no Tailscale login URL returned")


def logout(timeout:float=8.0) -> tuple[bool,str]:
    if not available():return False,"tailscale CLI unavailable"
    try:r=subprocess.run(["tailscale","logout"],capture_output=True,text=True,timeout=timeout)
    except (OSError,subprocess.TimeoutExpired) as e:return False,str(e)
    return r.returncode==0,(r.stdout.strip() or r.stderr.strip() or "logged out")
