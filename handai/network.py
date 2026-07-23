"""WLAN control for the device - thin wrapper over wpa_cli / iw.

On a handheld with no keyboard, joining WiFi has to work from the cockpit with
the OSK. Everything here is best-effort and guarded: on a dev box without
wpa_cli the functions report "unavailable" instead of crashing, so the cockpit
degrades gracefully.

Interface is auto-detected (first wireless device in /sys), overridable via
$HANDAI_WIFI_IFACE. The bring-up script (net/up.sh) writes the chosen iface to
$HANDAI_STATE/iface so the cockpit and the supplicant always agree.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

_last_scan_error = ""


def _sys_is_wireless(name: str) -> bool:
    base = Path("/sys/class/net") / name
    return (base / "wireless").exists() or (base / "phy80211").exists()


def detect_iface(names: list[str], is_wireless=_sys_is_wireless) -> str | None:
    """Pick a wireless interface: first that /sys says is wireless, else the
    first with a conventional wlan-ish name. Pure given `is_wireless` -> testable."""
    for n in names:
        if is_wireless(n):
            return n
    for n in names:
        if n.startswith(("wlan", "wlp", "wl")):
            return n
    return None


def _list_ifaces() -> list[str]:
    try:
        return sorted(p.name for p in Path("/sys/class/net").iterdir())
    except OSError:
        return []


def _iface_file() -> Path:
    state = os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai")
    return Path(state) / "iface"


def _iface() -> str:
    # explicit override wins; then the file net/up.sh wrote; then live detection
    env = os.environ.get("HANDAI_WIFI_IFACE")
    if env:
        return env
    f = _iface_file()
    if f.exists():
        name = f.read_text("utf-8").strip()
        if name:
            return name
    return detect_iface(_list_ifaces()) or "wlan0"


def available() -> bool:
    return shutil.which("wpa_cli") is not None


@dataclass(frozen=True)
class Network:
    ssid: str
    signal: int  # dBm, higher (closer to 0) is stronger
    secured: bool


def _wpa(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["wpa_cli", "-i", _iface(), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def parse_scan_results(text: str) -> list[Network]:
    """Parse `wpa_cli scan_results` output -> networks, strongest per SSID first.

    Pure function (no I/O) so it can be unit-tested without a radio. Format is
    tab-separated: bssid / frequency / signal(dBm) / flags / ssid.
    """
    nets: dict[str, Network] = {}
    for line in text.splitlines()[1:]:  # skip header row
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        _bssid, _freq, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not ssid:
            continue  # hidden SSID
        secured = any(x in flags for x in ("WPA", "WEP", "RSN"))
        try:
            sig = int(signal)
        except ValueError:
            sig = -100
        cur = nets.get(ssid)
        if cur is None or sig > cur.signal:  # keep strongest per SSID
            nets[ssid] = Network(ssid=ssid, signal=sig, secured=secured)
    return sorted(nets.values(), key=lambda n: n.signal, reverse=True)


def scan() -> list[Network]:
    global _last_scan_error
    _last_scan_error = ""
    if not available():
        _last_scan_error = "WPA_CLI IS NOT INSTALLED"
        return []
    iface = _iface()
    if not (Path("/sys/class/net") / iface).exists():
        _last_scan_error = f"WIFI INTERFACE {iface} NOT FOUND"
        return []
    rc, pong = _wpa("ping")
    if rc != 0 or "PONG" not in pong:
        _last_scan_error = f"WPA_SUPPLICANT NOT READY ON {iface}"
        return []
    rc, response = _wpa("scan")
    if rc != 0 or "FAIL" in response:
        _last_scan_error = f"WIFI SCAN COULD NOT START ON {iface}"
        return []
    # SDIO radios can take several seconds to report their first completed scan.
    for _ in range(16):
        time.sleep(0.5)
        rc, out = _wpa("scan_results")
        if rc != 0:
            continue
        networks = parse_scan_results(out)
        if networks:
            return networks
    _last_scan_error = f"NO VISIBLE WIFI NETWORKS FOUND ON {iface}"
    return []


def scan_error() -> str:
    return _last_scan_error


def connect(ssid: str, psk: str | None, timeout_s: int = 20) -> bool:
    """Add/enable a network and persist it. psk=None for open networks.

    Reuses an existing saved entry for the same SSID instead of stacking
    duplicates, so re-entering a password just updates it.
    """
    if not available():
        return False
    net_id = _find_saved(ssid)
    if net_id is None:
        rc, out = _wpa("add_network")
        tok = out.strip().split()[-1] if out.strip() else ""
        if rc != 0 or not tok.isdigit():
            return False
        net_id = tok
    # ssid/psk must be quoted for wpa_cli; it treats bare tokens as hex.
    _wpa("set_network", net_id, "ssid", f'"{ssid}"')
    if psk:
        _wpa("set_network", net_id, "psk", f'"{psk}"')
    else:
        _wpa("set_network", net_id, "key_mgmt", "NONE")
    _wpa("select_network", net_id)  # enables this one, disables others for the attempt
    _wpa("enable_network", net_id)
    _wpa("save_config")
    _wpa("reassociate")
    # wait for association + DHCP
    for _ in range(timeout_s):
        if "wpa_state=COMPLETED" in status_raw():
            return True
        time.sleep(1.0)
    return "wpa_state=COMPLETED" in status_raw()


def reconnect(ssid: str, timeout_s: int = 20) -> bool:
    """Re-associate to an already-saved network WITHOUT touching its credentials
    (unlike connect(), which reconfigures ssid/psk)."""
    if not available():
        return False
    nid = _find_saved(ssid)
    if nid is None:
        return False
    _wpa("select_network", nid)
    _wpa("enable_network", nid)
    _wpa("reassociate")
    for _ in range(timeout_s):
        if "wpa_state=COMPLETED" in status_raw():
            return True
        time.sleep(1.0)
    return "wpa_state=COMPLETED" in status_raw()


def parse_saved_networks(text: str) -> list[tuple[str, str, str]]:
    """Parse `wpa_cli list_networks` -> [(id, ssid, flags)]. Pure -> testable.

    Format is tab-separated: network id / ssid / bssid / flags.
    """
    out: list[tuple[str, str, str]] = []
    for line in text.splitlines()[1:]:  # skip header
        parts = line.split("\t")
        if len(parts) < 2 or not parts[0].strip().isdigit():
            continue
        nid = parts[0].strip()
        ssid = parts[1].strip()
        flags = parts[3].strip() if len(parts) > 3 else ""
        out.append((nid, ssid, flags))
    return out


def saved() -> list[str]:
    """SSIDs of saved networks (empty if wpa unavailable)."""
    if not available():
        return []
    rc, out = _wpa("list_networks")
    if rc != 0:
        return []
    return [ssid for _id, ssid, _flags in parse_saved_networks(out) if ssid]


def _find_saved(ssid: str) -> str | None:
    if not available():
        return None
    _rc, out = _wpa("list_networks")
    for nid, s, _flags in parse_saved_networks(out):
        if s == ssid:
            return nid
    return None


def forget(ssid: str) -> bool:
    """Remove a saved network and persist the change."""
    nid = _find_saved(ssid)
    if nid is None:
        return False
    rc, _ = _wpa("remove_network", nid)
    _wpa("save_config")
    return rc == 0


def status_raw() -> str:
    _rc, out = _wpa("status")
    return out


def status() -> str:
    """Human one-liner for the status bar."""
    if not available():
        return "wifi: n/a (no wpa_cli)"
    raw = status_raw()
    fields = dict(
        line.split("=", 1) for line in raw.splitlines() if "=" in line
    )
    state = fields.get("wpa_state", "?")
    ssid = fields.get("ssid", "")
    ip = fields.get("ip_address", "")
    if state == "COMPLETED":
        return f"wifi: {ssid} {ip}".strip()
    return f"wifi: {state.lower()}"
