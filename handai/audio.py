"""Microphone capture, Bluetooth headset pairing and local speech-to-text.

The module intentionally shells out to the small native tools already used by
the image. PipeWire supplies Bluetooth HFP/HSP microphones, ALSA is the direct
fallback for built-in and USB capture devices, and whisper.cpp performs fully
local transcription without an API credential.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import struct
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from . import preferences

MODEL_URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-tiny-q5_1.bin"
MODEL_SHA256 = "818710568da3ca15689e31a743197b520007872ff9576237bda97bd1b469c3d7"
_ALSA_VOLUME_CACHE: dict[bool, tuple[str, str]] = {}
_LAST_AUDIO_ERROR = ("", 0.0)


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


@dataclass(frozen=True)
class AudioSink:
    id: str
    label: str
    backend: str


@dataclass(frozen=True)
class VolumeState:
    percent: int
    muted: bool
    backend: str


@dataclass(frozen=True)
class SignalTest:
    duration: float
    rms_percent: int
    peak_percent: int
    clipped: bool
    silent: bool


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


def parse_pipewire_sinks(text: str) -> list[AudioSink]:
    try:
        objects = json.loads(text)
    except (ValueError, TypeError):
        return []
    found: list[AudioSink] = []
    for obj in objects if isinstance(objects, list) else []:
        props = ((obj.get("info") or {}).get("props") or {})
        if props.get("media.class") != "Audio/Sink":
            continue
        sink_id = str(obj.get("id") or props.get("object.serial") or "")
        if not sink_id:
            continue
        label = str(props.get("node.description") or props.get("device.description") or
                    props.get("node.name") or sink_id)
        found.append(AudioSink(sink_id, label, "pipewire"))
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


def parse_dshow_devices(text: str) -> list[AudioSource]:
    found: list[AudioSource] = []
    for line in str(text).splitlines():
        match = re.search(r'\]\s+"([^"]+)"\s+\(audio\)\s*$', line)
        if match:
            label = match.group(1)
            found.append(AudioSource(label, label, "dshow"))
    return found


def _run(argv: list[str], timeout: float = 8.0) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def list_sources() -> list[AudioSource]:
    sources: list[AudioSource] = []
    if os.name == "nt" and shutil.which("ffmpeg"):
        result = _run(["ffmpeg", "-hide_banner", "-list_devices", "true",
                       "-f", "dshow", "-i", "dummy"])
        if result:
            sources.extend(parse_dshow_devices(result.stderr))
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


def list_sinks() -> list[AudioSink]:
    sinks: list[AudioSink] = []
    if shutil.which("pw-dump"):
        result = _run(["pw-dump"])
        if result and result.returncode == 0:
            sinks.extend(parse_pipewire_sinks(result.stdout))
    if not sinks and shutil.which("aplay"):
        result = _run(["aplay", "-L"])
        if result and result.returncode == 0:
            for source in parse_arecord_list(result.stdout):
                sinks.append(AudioSink(source.id, source.label, "alsa"))
    return sinks


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


def selected_sink(sinks: list[AudioSink] | None = None) -> AudioSink | None:
    choices = sinks if sinks is not None else list_sinks()
    saved = preferences.load().get("audio_sink")
    return next((item for item in choices if f"{item.backend}:{item.id}" == saved), None) or (
        choices[0] if choices else None
    )


def save_sink(sink: AudioSink) -> tuple[bool, str]:
    if sink.backend == "pipewire" and shutil.which("wpctl"):
        result = _run(["wpctl", "set-default", sink.id])
        if not result or result.returncode != 0:
            return False, ((result.stderr or result.stdout).strip() if result else "SET DEFAULT FAILED")
    data = preferences.load()
    data["audio_sink"] = f"{sink.backend}:{sink.id}"
    preferences.save(data)
    return True, f"OUTPUT: {sink.label}"


def parse_wpctl_volume(text: str) -> VolumeState | None:
    match = re.search(r"Volume:\s*([0-9]+(?:\.[0-9]+)?)", str(text))
    if not match:
        return None
    return VolumeState(max(0, min(150, round(float(match.group(1)) * 100))),
                       "[MUTED]" in str(text).upper(), "pipewire")


def parse_amixer_volume(text: str) -> VolumeState | None:
    # H700 DAC controls often expose a percentage but no playback switch.
    # Desktop/USB mixers commonly append [on]/[off]. Both are valid controls.
    percentages = re.findall(r"\[(\d{1,3})%\]", str(text))
    if not percentages:
        return None
    switches = re.findall(r"\[(on|off)\]", str(text), re.IGNORECASE)
    return VolumeState(
        max(0, min(100, int(percentages[-1]))),
        bool(switches and switches[-1].casefold() == "off"),
        "alsa",
    )


def parse_amixer_controls(text: str) -> list[str]:
    """Return simple mixer control names in the order reported by ALSA."""
    return re.findall(r"Simple mixer control '([^']+)'", str(text))


def _log_audio_error(detail: str) -> None:
    """Persist a rate-limited mixer snapshot to the SD card's FAT partition."""
    global _LAST_AUDIO_ERROR
    message = " ".join(str(detail).split())[:160] or "unknown audio error"
    now = time.monotonic()
    if message == _LAST_AUDIO_ERROR[0] and now - _LAST_AUDIO_ERROR[1] < 10:
        return
    _LAST_AUDIO_ERROR = (message, now)
    helper = "/usr/sbin/handai-boot-log"
    if not os.path.exists(helper):
        return
    try:
        subprocess.run(
            [helper, "AUDIO_ERROR", message],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=8,
        )
    except (OSError, subprocess.TimeoutExpired):
        pass


def _alsa_volume_controls(is_input: bool) -> list[tuple[str, str, VolumeState]]:
    """Discover a usable volume control instead of assuming desktop 'Master'.

    The H700 codec normally exposes DAC/Line Out controls, while USB headsets
    commonly expose Speaker/Headphone or Capture.  Probe all likely cards and
    keep only controls whose current value can actually be parsed.
    """
    preferred = (
        ("Capture", "Mic", "ADC", "Input") if is_input else
        ("Master", "PCM", "DAC", "Digital", "Line Out", "Lineout",
         "Headphone", "Speaker")
    )
    cached = _ALSA_VOLUME_CACHE.get(is_input)
    if cached:
        card, name = cached
        result = _run(["amixer", "-c", card, "sget", name])
        parsed = parse_amixer_volume(
            result.stdout if result and result.returncode == 0 else ""
        )
        if parsed:
            return [(card, name, parsed)]
        _ALSA_VOLUME_CACHE.pop(is_input, None)
    for card in map(str, range(4)):
        listing = _run(["amixer", "-c", card, "scontrols"])
        if not listing or listing.returncode != 0:
            continue
        names = parse_amixer_controls(listing.stdout)
        ordered = sorted(
            names,
            key=lambda name: next(
                (index for index, token in enumerate(preferred)
                 if token.casefold() in name.casefold()),
                len(preferred),
            ),
        )
        for name in ordered:
            if not any(token.casefold() in name.casefold() for token in preferred):
                continue
            result = _run(["amixer", "-c", card, "sget", name])
            parsed = parse_amixer_volume(
                result.stdout if result and result.returncode == 0 else ""
            )
            if parsed:
                _ALSA_VOLUME_CACHE[is_input] = (card, name)
                return [(card, name, parsed)]
    return []


def get_volume(kind: str, source: AudioSource | None = None,
               sink: AudioSink | None = None) -> VolumeState:
    is_input = kind == "input"
    selected_backend = source.backend if is_input and source else sink.backend if sink else None
    if shutil.which("wpctl") and selected_backend != "alsa":
        target = (source.id if is_input and source and source.backend == "pipewire"
                  else sink.id if not is_input and sink and sink.backend == "pipewire"
                  else "@DEFAULT_AUDIO_SOURCE@" if is_input else "@DEFAULT_AUDIO_SINK@")
        result = _run(["wpctl", "get-volume", target])
        parsed = parse_wpctl_volume(result.stdout if result and result.returncode == 0 else "")
        if parsed:
            return parsed
    if shutil.which("amixer"):
        controls = _alsa_volume_controls(is_input)
        if controls:
            return controls[0][2]
    # A neutral midpoint makes the first key press useful even while an audio
    # service is still appearing; set_volume will still report a real failure.
    return VolumeState(50, False, "unavailable")


def set_volume(kind: str, percent: int, muted: bool = False,
               source: AudioSource | None = None, sink: AudioSink | None = None) -> tuple[bool, str]:
    value = max(0, min(150 if kind == "output" else 100, int(percent)))
    is_input = kind == "input"
    selected_backend = source.backend if is_input and source else sink.backend if sink else None
    errors: list[str] = []
    if shutil.which("wpctl") and selected_backend != "alsa":
        target = (source.id if is_input and source and source.backend == "pipewire"
                  else sink.id if not is_input and sink and sink.backend == "pipewire"
                  else "@DEFAULT_AUDIO_SOURCE@" if is_input else "@DEFAULT_AUDIO_SINK@")
        volume = _run(["wpctl", "set-volume", target, f"{value}%"])
        mute = _run(["wpctl", "set-mute", target, "1" if muted else "0"])
        if volume and mute and volume.returncode == 0 and mute.returncode == 0:
            return True, f"{'MIC' if is_input else 'OUTPUT'} {value}%"
        detail = ((volume.stderr if volume else "") or
                  (mute.stderr if mute else "") or
                  (volume.stdout if volume else "") or
                  (mute.stdout if mute else "")).strip()
        errors.append(detail or "PIPEWIRE HAS NO ACTIVE OUTPUT")
    if shutil.which("amixer"):
        controls = _alsa_volume_controls(is_input)
        for card, control, _state in controls:
            switch = ("cap" if is_input and not muted else
                      "nocap" if is_input else
                      "unmute" if not muted else "mute")
            attempts = [
                ["amixer", "-c", card, "sset", control, f"{value}%", switch],
                # Codec volume controls without a mute switch reject
                # "unmute"; setting the percentage alone is correct.
                ["amixer", "-c", card, "sset", control,
                 f"{0 if muted else value}%"],
            ]
            for argv in attempts:
                result = _run(argv)
                if result and result.returncode == 0:
                    return True, (
                        f"{'MIC' if is_input else 'OUTPUT'} "
                        f"{'MUTED' if muted else f'{value}%'}"
                    )
                if result:
                    errors.append((result.stderr or result.stdout).strip())
        if not controls:
            errors.append("NO ALSA VOLUME CONTROL")
    detail = next((line for line in errors if line), "NO AUDIO MIXER FOUND")
    detail = detail[:160]
    _log_audio_error(detail)
    return False, detail


def record_argv(source: AudioSource, target: Path) -> list[str]:
    if source.backend == "pipewire":
        return [
            "pw-record", f"--target={source.id}", "--rate=16000", "--channels=1",
            "--format=s16", str(target),
        ]
    if source.backend == "dshow":
        return [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "dshow", "-i", f"audio={source.id}", "-ac", "1",
            "-ar", "16000", "-sample_fmt", "s16", str(target),
        ]
    return [
        "arecord", "-q", "-D", source.id, "-f", "S16_LE", "-r", "16000",
        "-c", "1", "-t", "wav", str(target),
    ]


def start_recording(source: AudioSource, target: Path | None = None) -> subprocess.Popen[bytes]:
    output = target or recording_path()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.unlink(missing_ok=True)
    return subprocess.Popen(record_argv(source, output), stdin=subprocess.PIPE if source.backend == "dshow" else None,
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def stop_recording(process: subprocess.Popen[bytes], timeout: float = 3.0) -> tuple[bool, str]:
    if process.stdin:
        try:
            process.stdin.write(b"q\n")
            process.stdin.flush()
        except OSError:
            process.terminate()
    else:
        process.terminate()
    try:
        _, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        _, stderr = process.communicate()
    message = (stderr or b"").decode("utf-8", "replace").strip()
    # SIGTERM is the normal end of an open-ended arecord/pw-record capture.
    return (process.returncode in (0, -15, 1), message)


def analyze_wav(path: Path) -> SignalTest:
    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        rate = wav.getframerate()
        frames = wav.getnframes()
        if width != 2 or channels < 1 or rate <= 0:
            raise ValueError("MIC TEST EXPECTS 16-BIT PCM")
        raw = wav.readframes(frames)
    count = len(raw) // 2
    if not count:
        return SignalTest(0.0, 0, 0, False, True)
    values = (sample[0] for sample in struct.iter_unpack("<h", raw))
    sum_squares = 0
    peak = 0
    for value in values:
        absolute = abs(value)
        peak = max(peak, absolute)
        sum_squares += value * value
    rms = math.sqrt(sum_squares / count)
    rms_percent = min(100, round(rms / 32767 * 100))
    peak_percent = min(100, round(peak / 32767 * 100))
    return SignalTest(frames / rate, rms_percent, peak_percent,
                      peak_percent >= 98, rms_percent < 1)


def make_test_tone(path: Path | None = None, frequency: int = 660,
                   duration: float = 0.7) -> Path:
    target = path or state_dir() / "speaker-test.wav"
    target.parent.mkdir(parents=True, exist_ok=True)
    rate = 16000
    frames = int(rate * duration)
    with wave.open(str(target), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        samples = (int(10000 * math.sin(2 * math.pi * frequency * i / rate))
                   for i in range(frames))
        wav.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return target


def play_audio(path: Path, sink: AudioSink | None = None, timeout: float = 20.0) -> tuple[bool, str]:
    if sink and sink.backend == "pipewire" and shutil.which("pw-play"):
        argv = ["pw-play", f"--target={sink.id}", str(path)]
    elif shutil.which("pw-play"):
        argv = ["pw-play", str(path)]
    elif shutil.which("aplay"):
        argv = ["aplay", "-q"]
        if sink and sink.backend == "alsa":
            argv.extend(["-D", sink.id])
        argv.append(str(path))
    elif os.name == "nt" and shutil.which("ffplay"):
        argv = ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    else:
        return False, "NO AUDIO PLAYER FOUND"
    result = _run(argv, timeout)
    if not result or result.returncode != 0:
        return False, ((result.stderr or result.stdout).strip() if result else "PLAYBACK FAILED")
    return True, "PLAYBACK COMPLETE"


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
