"""On-device RG35XXSP acceptance report.

The report deliberately reads kernel/userland state without changing it.  It can
be run over SSH after the first boot and leaves a JSON artifact under /data.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Result:
    name: str
    ok: bool
    required: bool
    detail: str


def _read(path: Path) -> str:
    try:
        return path.read_text("utf-8", errors="replace").replace("\x00", " ").strip()
    except OSError:
        return ""


def _glob_text(root: Path, pattern: str, filename: str) -> list[str]:
    return [_read(item / filename) for item in root.glob(pattern) if _read(item / filename)]


def _command(argv: list[str], timeout: float = 5.0) -> tuple[int, str]:
    try:
        done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return done.returncode, (done.stdout or done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, str(exc)


def collect(root: Path = Path("/")) -> list[Result]:
    """Collect checks. ``root`` exists for deterministic fixture-based tests."""
    sys = root / "sys"
    dev = root / "dev"
    proc = root / "proc"
    data = root / "data"
    results: list[Result] = []

    model = _read(sys / "firmware/devicetree/base/model") or "unknown"
    compatible = _read(sys / "firmware/devicetree/base/compatible")
    board_ok = "rg35" in (model + compatible).lower() or root != Path("/")
    results.append(Result("board", board_ok, True, model))

    fb_size = _read(sys / "class/graphics/fb0/virtual_size")
    fb_bpp = _read(sys / "class/graphics/fb0/bits_per_pixel")
    drm = sorted(p.name for p in (dev / "dri").glob("card*")) if (dev / "dri").exists() else []
    display_ok = bool(fb_size or drm or (dev / "fb0").exists())
    results.append(Result("display", display_ok, True,
                          f"fb={fb_size or '-'} bpp={fb_bpp or '-'} drm={','.join(drm) or '-'}"))

    input_text = _read(proc / "bus/input/devices")
    events = sorted(p.name for p in (dev / "input").glob("event*")) if (dev / "input").exists() else []
    results.append(Result("input", bool(events), True,
                          f"events={','.join(events) or '-'} devices={'present' if input_text else 'unknown'}"))
    input_folded = input_text.casefold()
    gamepad = any(marker in input_folded for marker in
                  ("deeplay", "gamepad", "controller", "rg35xx"))
    results.append(Result("gamepad", gamepad, True,
                          "built-in controller detected" if gamepad
                          else "no Deeplay/RG35XX controller in /proc/bus/input/devices"))
    lid = "SW_LID" in input_text or "Lid Switch" in input_text
    results.append(Result("lid switch", lid, False, "detected" if lid else "not exposed by kernel"))

    ifaces = sorted(p.name for p in (sys / "class/net").glob("*"))
    wireless = [name for name in ifaces if
                (sys / "class/net" / name / "wireless").exists() or
                (sys / "class/net" / name / "phy80211").exists() or
                name.startswith(("wlan", "wlp", "wl"))]
    results.append(Result("wifi", bool(wireless), True, ",".join(wireless) or "no wireless interface"))
    bluetooth = sorted(p.name for p in (sys / "class/bluetooth").glob("hci*"))
    results.append(Result("bluetooth radio", bool(bluetooth), True,
                          ",".join(bluetooth) or "no Bluetooth HCI controller"))

    batteries = _glob_text(sys / "class/power_supply", "*", "capacity")
    statuses = _glob_text(sys / "class/power_supply", "*", "status")
    results.append(Result("battery", bool(batteries), False,
                          f"capacity={','.join(batteries) or '-'} status={','.join(statuses) or '-'}"))

    mounts = _read(proc / "mounts")
    mounted = any(parts[1] == "/data" for line in mounts.splitlines()
                  if len(parts := line.split()) >= 2)
    writable = data.is_dir() and os.access(data, os.W_OK)
    results.append(Result("persistent data", mounted and writable, True,
                          f"mounted={mounted} writable={writable}"))

    modules = root / "lib/modules/4.9.170"
    firmware = root / "lib/firmware"
    results.append(Result("vendor modules", modules.is_dir(), True, str(modules)))
    results.append(Result("vendor firmware", firmware.is_dir() and any(firmware.rglob("*")), True,
                          str(firmware)))

    for command in ("handai", "python3", "ssh", "tmux", "tailscale", "tailscaled",
                    "qrencode", "rtk_hciattach"):
        found = shutil.which(command) if root == Path("/") else next(
            (str(root / base / command) for base in ("usr/bin", "usr/sbin", "bin", "sbin")
             if (root / base / command).exists()), None)
        results.append(Result(command, found is not None, True, found or "missing"))

    audio_commands = []
    for command in ("arecord", "pw-record", "wireplumber", "bluetoothctl", "whisper-cli"):
        found = shutil.which(command) if root == Path("/") else next(
            (str(root / base / command) for base in ("usr/bin", "usr/sbin", "bin", "sbin")
             if (root / base / command).exists()), None)
        audio_commands.append(f"{command}={'yes' if found else 'no'}")
    results.append(Result("voice input", all(item.endswith("=yes") for item in audio_commands),
                          False, " ".join(audio_commands)))

    if root == Path("/"):
        audio_socket = Path(os.environ.get(
            "PIPEWIRE_RUNTIME_DIR", "/run/handai-audio"
        )) / "pipewire-0"
        results.append(Result(
            "audio daemon", audio_socket.exists(), True, str(audio_socket)
        ))
        if wireless and shutil.which("wpa_cli"):
            code, output = _command(["wpa_cli", "-i", wireless[0], "ping"])
            results.append(Result(
                "wifi control", code == 0 and "PONG" in output, True,
                output[:160] or "wpa_supplicant did not answer",
            ))

    if root == Path("/") and shutil.which("tailscale"):
        code, output = _command(["tailscale", "status", "--json"])
        results.append(Result("tailscale daemon", code == 0, False,
                              "reachable" if code == 0 else output[:160]))

    return results


def build_report(results: list[Result]) -> dict:
    required_ok = all(item.ok for item in results if item.required)
    return {
        "schema": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "hostname": socket.gethostname(),
        "required_ok": required_ok,
        "checks": [asdict(item) for item in results],
    }


def save(report: dict, destination: Path | None = None) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    fallback = "/data/handai" if os.name == "posix" else os.path.expanduser("~/.local/state/handai")
    target = destination or Path(os.environ.get("HANDAI_STATE", fallback)) / f"hardware-report-{stamp}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2) + "\n", "utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate a read-only HandAI hardware acceptance report")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json", action="store_true", help="also print the complete JSON report")
    args = parser.parse_args(argv)
    report = build_report(collect())
    target = save(report, args.output)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for item in report["checks"]:
            state = "PASS" if item["ok"] else ("FAIL" if item["required"] else "WARN")
            print(f"{state:4} {item['name']}: {item['detail']}")
    print(f"REPORT {target}")
    return 0 if report["required_ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
