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
import re
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
    security: str = "open"  # open | wpa | sae | wep | enterprise


def _wpa(*args: str, timeout: float = 8.0) -> tuple[int, str]:
    try:
        r = subprocess.run(
            ["wpa_cli", "-i", _iface(), *args],
            capture_output=True, text=True, timeout=timeout,
        )
        return r.returncode, r.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


def _bring_up() -> bool:
    """Retry the board bring-up hook when the GUI beats the boot-time worker."""
    script = Path(os.environ.get("HANDAI_NET_UP", "/opt/handai/net/up.sh"))
    if not script.is_file():
        return False
    try:
        result = subprocess.run(
            [str(script)], capture_output=True, text=True, timeout=25.0
        )
        return result.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _ensure_control() -> tuple[bool, str]:
    """Ensure the radio and wpa_supplicant control socket are ready.

    S99handai starts networking in the background so boot remains responsive.
    A fast user can therefore open Network before the SDIO driver has appeared.
    Retry the same idempotent bring-up script synchronously instead of reporting
    an empty scan in that race.
    """
    iface = _iface()
    if (Path("/sys/class/net") / iface).exists():
        rc, pong = _wpa("ping")
        if rc == 0 and "PONG" in pong:
            return True, iface
    _bring_up()
    iface = _iface()
    if not (Path("/sys/class/net") / iface).exists():
        return False, f"WIFI INTERFACE {iface} NOT FOUND"
    for _ in range(20):
        rc, pong = _wpa("ping")
        if rc == 0 and "PONG" in pong:
            return True, iface
        time.sleep(0.25)
    return False, f"WPA_SUPPLICANT NOT READY ON {iface}"


def parse_scan_results(text: str) -> list[Network]:
    """Parse `wpa_cli scan_results` output -> networks, strongest per SSID first.

    Pure function (no I/O) so it can be unit-tested without a radio. Format is
    tab-separated: bssid / frequency / signal(dBm) / flags / ssid.
    """
    nets: dict[str, Network] = {}
    # Parse every line instead of blindly dropping the first one. Depending on
    # the wpa_cli build, informational text such as "Selected interface ..." can
    # precede the header (or the header can be omitted entirely).
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) < 5:
            continue
        _bssid, _freq, signal, flags, ssid = parts[0], parts[1], parts[2], parts[3], parts[4]
        if not ssid:
            continue  # hidden SSID
        if "EAP" in flags:
            security = "enterprise"
        elif "WEP" in flags:
            security = "wep"
        elif "SAE" in flags and "PSK" not in flags:
            security = "sae"
        elif any(x in flags for x in ("WPA", "RSN", "PSK")):
            security = "wpa"
        else:
            security = "open"
        secured = security != "open"
        try:
            sig = int(signal)
        except ValueError:
            sig = -100
        cur = nets.get(ssid)
        if cur is None or sig > cur.signal:  # keep strongest per SSID
            nets[ssid] = Network(ssid=ssid, signal=sig, secured=secured,
                                 security=security)
    return sorted(nets.values(), key=lambda n: n.signal, reverse=True)


def _diag_output(argv: list[str]) -> str:
    try:
        result = subprocess.run(
            argv, capture_output=True, text=True, timeout=4.0
        )
        return (result.stdout + result.stderr).strip()
    except (OSError, subprocess.TimeoutExpired):
        return "unavailable"


def _record_scan_failure(detail: str, scan_output: str = "") -> None:
    """Persist credential-free radio evidence for the next SD-card diagnosis."""
    state = Path(os.environ.get("HANDAI_STATE") or
                 os.path.expanduser("~/.local/state/handai"))
    iface = _iface()
    try:
        state.mkdir(parents=True, exist_ok=True)
        report = [
            f"ERROR: {detail}",
            f"INTERFACE: {iface}",
            "--- WPA STATUS ---",
            _diag_output(["wpa_cli", "-i", iface, "status"]),
            "--- WPA SCAN RESULTS ---",
            scan_output.strip() or "(empty)",
            "--- RFKILL ---",
            _diag_output(["rfkill", "list"]),
            "--- IW DEV ---",
            _diag_output(["iw", "dev"]),
            "--- IP LINK ---",
            _diag_output(["ip", "-details", "link", "show", "dev", iface]),
            "--- KERNEL WIFI TAIL ---",
            _diag_output(["dmesg"]),
        ]
        # Keep the useful tail of dmesg and bound the persistent report.
        report[-1] = "\n".join(report[-1].splitlines()[-120:])
        (state / "wifi-scan-latest.log").write_text(
            "\n".join(report)[-65536:] + "\n", "utf-8"
        )
    except OSError:
        pass
    boot_log = Path("/usr/sbin/handai-boot-log")
    if boot_log.is_file():
        try:
            subprocess.run(
                [str(boot_log), "WIFI_ERROR", detail],
                capture_output=True, text=True, timeout=20.0,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass


def scan() -> list[Network]:
    global _last_scan_error
    _last_scan_error = ""
    if not available():
        _last_scan_error = "WPA_CLI IS NOT INSTALLED"
        _record_scan_failure(_last_scan_error)
        return []
    ready, detail = _ensure_control()
    if not ready:
        _last_scan_error = detail
        _record_scan_failure(_last_scan_error)
        return []
    iface = detail
    rc, response = _wpa("scan")
    busy = "FAIL-BUSY" in response
    if (rc != 0 or "FAIL" in response) and not busy:
        _last_scan_error = f"WIFI SCAN COULD NOT START ON {iface}"
        _record_scan_failure(_last_scan_error, response)
        return []
    # SDIO radios can take several seconds to report their first completed scan.
    last_output = ""
    for attempt in range(20):
        time.sleep(0.5)
        rc, out = _wpa("scan_results")
        last_output = out
        if rc != 0:
            continue
        networks = parse_scan_results(out)
        if networks:
            return networks
        # A driver can report FAIL-BUSY for an old scan and then return an empty
        # cache. Trigger one fresh scan after that operation has had time to end.
        if attempt == 9:
            _wpa("scan")
    _last_scan_error = f"NO VISIBLE WIFI NETWORKS FOUND ON {iface}"
    _record_scan_failure(_last_scan_error, last_output)
    return []


def scan_error() -> str:
    return _last_scan_error


def _wpa_string(value: str) -> str:
    """Encode one literal for wpa_supplicant's quoted-string grammar."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _psk_value(value: str) -> str:
    encoded = value.encode("utf-8")
    if len(value) == 64 and re.fullmatch(r"[0-9A-Fa-f]{64}", value):
        return value
    if not 8 <= len(encoded) <= 63:
        raise ValueError("WPA PASSPHRASE MUST BE 8-63 BYTES OR 64 HEX DIGITS")
    return _wpa_string(value)


def _wep_value(value: str) -> str:
    if len(value) in (10, 26) and re.fullmatch(r"[0-9A-Fa-f]+", value):
        return value
    if len(value.encode("utf-8")) not in (5, 13):
        raise ValueError("WEP KEY MUST BE 5/13 TEXT BYTES OR 10/26 HEX DIGITS")
    return _wpa_string(value)


def _set_network(net_id: str, field: str, value: str) -> bool:
    rc, output = _wpa("set_network", net_id, field, value)
    return rc == 0 and "FAIL" not in output


def connect(ssid: str, psk: str | None, timeout_s: int = 20,
            security: str | None = None) -> bool:
    """Add/enable a network and persist it. psk=None for open networks.

    Reuses an existing saved entry for the same SSID instead of stacking
    duplicates, so re-entering a password just updates it.
    """
    if not available():
        return False
    kind = security or ("wpa" if psk else "open")
    if kind == "enterprise":
        return False
    net_id = _find_saved(ssid)
    created = net_id is None
    if net_id is None:
        rc, out = _wpa("add_network")
        tok = out.strip().split()[-1] if out.strip() else ""
        if rc != 0 or not tok.isdigit():
            return False
        net_id = tok
    try:
        configured = _set_network(net_id, "ssid", _wpa_string(ssid))
        if kind == "wep" and psk is not None:
            configured = (configured and
                          _set_network(net_id, "key_mgmt", "NONE") and
                          _set_network(net_id, "wep_key0", _wep_value(psk)) and
                          _set_network(net_id, "wep_tx_keyidx", "0"))
        elif kind == "sae" and psk is not None:
            configured = (configured and
                          _set_network(net_id, "key_mgmt", "SAE") and
                          _set_network(net_id, "sae_password", _wpa_string(psk)))
        elif psk is not None:
            configured = (configured and
                          _set_network(net_id, "key_mgmt", "WPA-PSK") and
                          _set_network(net_id, "psk", _psk_value(psk)))
        else:
            configured = configured and _set_network(net_id, "key_mgmt", "NONE")
    except ValueError:
        configured = False
    if not configured:
        if created:
            _wpa("remove_network", net_id)
        return False
    # select_network enables this one and disables others for the attempt.
    for action in (("select_network", net_id), ("enable_network", net_id),
                   ("save_config",), ("reassociate",)):
        rc, output = _wpa(*action)
        if rc != 0 or "FAIL" in output:
            return False
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
