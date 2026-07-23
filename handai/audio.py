"""Microphone capture, Bluetooth headset pairing and local speech-to-text.

The module intentionally shells out to the small native tools already used by
the image. PipeWire supplies Bluetooth HFP/HSP microphones, ALSA is the direct
fallback for built-in and USB capture devices, and whisper.cpp performs fully
local transcription without an API credential.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import preferences

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin"
MODEL_SHA256 = "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7"


@dataclass(frozen=True)
class AudioSource:
    id: str
    label: str
    backend: str


@dataclass(frozen=True)
class BluetoothDevice:
    address: str
    label: str
    connected: bool = False


def state_dir() -> Path:
    root = Path(os.environ.get("HANDAI_STATE") or os.path.expanduser("~/.local/state/handai"))
    return root / "voice"


def model_path() -> Path:
    return Path(os.environ.get("HANDAI_WHISPER_MODEL") or state_dir() / "ggml-tiny-q5_1.bin")


def recording_path() -> Path:
    target = state_dir() / "prompt.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def parse_pipewire_dump(text: str) -> list[AudioSource]:
    try:
        objects = json.loads(text)
    except (ValueError, TypeError):
        return []
    found: list[AudioSource] = []
    for obj in objects if isinstance(objects, list) else []:
        info = obj.get("info") or {}
        props = info.get("props") or {}
        if props.get("media.class") != "Audio/Source":
            continue
        source_id = str(props.get("node.name") or obj.get("id") or "")
        if not source_id:
            continue
        label = str(props.get("node.description") or props.get("device.description") or source_id)
        found.append(AudioSource(source_id, label, "pipewire"))
    return found


def parse_arecord_list(text: str) -> list[AudioSource]:
    found: list[AudioSource] = []
    for line in str(text).splitlines():
        if not line or line[0].isspace() or line.startswith(("null", "sysdefault")):
            continue
        device = line.strip()
        if device == "default" or device.startswith(("hw:", "plughw:", "usbstream:")):
            found.append(AudioSource(device, f"ALSA {device}", "alsa"))
    return found


def _run(argv: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def list_sources() -> list[AudioSource]:
    sources: list[AudioSource] = []
    if shutil.which("pw-dump"):
        result = _run(["pw-dump"])
        if result and result.returncode == 0:
            sources.extend(parse_pipewire_dump(result.stdout))
    if shutil.which("arecord"):
        result = _run(["arecord", "-L"])
        if result and result.returncode == 0:
            sources.extend(parse_arecord_list(result.stdout))
    unique: dict[tuple[str, str], AudioSource] = {}
    for source in sources:
        unique[(source.backend, source.id)] = source
    return list(unique.values())


def selected_source(sources: list[AudioSource] | None = None) -> AudioSource | None:
    choices = sources if sources is not None else list_sources()
    saved = preferences.load().get("voice_source")
    return next((item for item in choices if f"{item.backend}:{item.id}" == saved), None) or (
        choices[0] if choices else None
    )


def save_source(source: AudioSource) -> None:
    data = preferences.load()
    data["voice_source"] = f"{source.backend}:{source.id}"
    preferences.save(data)


def record_argv(source: AudioSource, target: Path) -> list[str]:
    if source.backend == "pipewire":
        return [
            "pw-record", f"--target={source.id}", "--rate=16000", "--channels=1",
            "--format=s16", str(target),
        ]
    return [
        "arecord", "-q", "-D", source.id, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-t", "wav", str(target),
    ]


def start_recording(source: AudioSource, target: Path | None = None) -> subprocess.Popen[bytes]:
    output = target or recording_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    return subprocess.Popen(record_argv(source, output), stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stop_recording(process: subprocess.Popen[bytes], timeout: float = 3.0) -> tuple[bool, str]:
    process.terminate()
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate()
    message = (stderr or b"").decode("utf-8", "replace").strip()
    # SIGTERM is the normal end of an open-ended arecord/pw-record capture.
    return (process.returncode in (0, -15, 1), message)


def whisper_available() -> bool:
    return bool(shutil.which(os.environ.get("HANDAI_WHISPER_CLI", "whisper-cli")))


def transcribe(wav: Path, language: str = "auto", timeout: float = 180.0) -> tuple[bool, str]:
    cli = shutil.which(os.environ.get("HANDAI_WHISPER_CLI", "whisper-cli"))
    model = model_path()
    if not cli:
        return False, "WHISPER-CLI IS NOT INSTALLED"
    if not model.exists():
        return False, "VOICE MODEL IS NOT INSTALLED"
    prefix = wav.with_suffix("")
    output = prefix.with_suffix(".txt")
    output.unlink(missing_ok=True)
    argv = [
        cli, "-m", str(model), "-f", str(wav), "--language", language,
        "--no-timestamps", "--output-txt", "--output-file", str(prefix),
    ]
    result = _run(argv, timeout)
    if not result:
        return False, "TRANSCRIPTION TIMED OUT OR FAILED TO START"
    if result.returncode != 0:
        return False, (result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "TRANSCRIPTION FAILED")
    try:
        text = output.read_text("utf-8").strip()
    except OSError:
        text = result.stdout.strip()
    return (bool(text), text or "NO SPEECH DETECTED")


def install_model(target: Path | None = None) -> tuple[bool, str]:
    destination = target or model_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="whisper-", suffix=".part", dir=destination.parent)
    os.close(fd)
    temp_path = Path(temporary)
    digest = hashlib.sha256()
    try:
        with urllib.request.urlopen(MODEL_URL, timeout=30) as response, temp_path.open("wb") as out:
            while chunk := response.read(1024 * 1024):
                out.write(chunk)
                digest.update(chunk)
        if digest.hexdigest().lower() != MODEL_SHA256:
            return False, "MODEL CHECKSUM MISMATCH"
        temp_path.replace(destination)
        return True, f"MODEL READY ({destination.stat().st_size // (1024 * 1024)} MB)"
    except (OSError, urllib.error.URLError) as exc:
        return False, f"MODEL DOWNLOAD FAILED: {exc}"
    finally:
        temp_path.unlink(missing_ok=True)


def parse_bluetooth_devices(text: str) -> list[BluetoothDevice]:
    found: list[BluetoothDevice] = []
    for line in str(text).splitlines():
        parts = line.strip().split(maxsplit=2)
        if len(parts) == 3 and parts[0] == "Device":
            found.append(BluetoothDevice(parts[1], parts[2]))
    return found


def bluetooth_devices(scan: bool = False) -> list[BluetoothDevice]:
    if not shutil.which("bluetoothctl"):
        return []
    if scan:
        _run(["bluetoothctl", "--timeout", "8", "scan", "on"], timeout=10)
        listing = _run(["bluetoothctl", "devices"])
    else:
        listing = _run(["bluetoothctl", "devices", "Paired"])
    devices = parse_bluetooth_devices(listing.stdout if listing else "")
    result: list[BluetoothDevice] = []
    for device in devices:
        info = _run(["bluetoothctl", "info", device.address])
        connected = bool(info and "Connected: yes" in info.stdout)
        result.append(BluetoothDevice(device.address, device.label, connected))
    return result


def connect_bluetooth(device: BluetoothDevice, pair: bool = False) -> tuple[bool, str]:
    if not shutil.which("bluetoothctl"):
        return False, "BLUETOOTHCTL IS NOT INSTALLED"
    if pair:
        paired = _run(["bluetoothctl", "--timeout", "25", "--agent", "NoInputNoOutput", "pair", device.address], 28)
        if not paired or paired.returncode != 0:
            return False, ((paired.stderr or paired.stdout).strip() if paired else "PAIRING FAILED")
        _run(["bluetoothctl", "trust", device.address])
    result = _run(["bluetoothctl", "connect", device.address], 15)
    if not result or result.returncode != 0:
        return False, ((result.stderr or result.stdout).strip() if result else "CONNECTION FAILED")
    return True, f"CONNECTED {device.label}"
