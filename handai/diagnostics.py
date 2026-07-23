"""Runtime preflight used on development hosts and real handheld hardware."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Check:
    name:str
    ok:bool
    detail:str


def run()->list[Check]:
    checks=[]
    for command in ("ssh","ssh-keygen","tmux","wpa_cli","tailscale","qrencode",
                    "arecord","aplay","wpctl","bluetoothctl","whisper-cli"):
        found=shutil.which(command);checks.append(Check(command,found is not None,found or "missing"))
    if os.name=="posix":
        checks.append(Check("display",Path("/dev/dri").exists() or Path("/dev/fb0").exists(),"DRM or framebuffer"))
        input_names=[]
        for event in Path("/sys/class/input").glob("event*"):
            try:input_names.append((event/"device/name").read_text("utf-8").strip())
            except OSError:continue
        controller=next((name for name in input_names if "controller" in name.casefold() or "deeplay" in name.casefold()),"")
        checks.append(Check("gamepad",bool(controller),controller or "controller input missing"))
        wireless=next((item.name for item in Path("/sys/class/net").glob("*")
                       if (item/"wireless").exists() or (item/"phy80211").exists()),"")
        checks.append(Check("wifi interface",bool(wireless),wireless or "wireless interface missing"))
        audio_socket=Path("/run/handai-audio/pipewire-0")
        checks.append(Check("audio service",audio_socket.exists(),str(audio_socket)))
        data=Path("/data")
        checks.append(Check("persistent data",data.is_dir() and os.access(data,os.W_OK),str(data)))
    else:
        checks.append(Check("development host",True,os.name))
    return checks


def summary(checks:list[Check]|None=None)->tuple[bool,list[str]]:
    values=checks or run()
    return all(item.ok for item in values),[f"{'OK' if item.ok else 'FAIL'} {item.name}: {item.detail}" for item in values]
