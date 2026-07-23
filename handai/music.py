"""Curated CC0 chiptune playback and provider-theme routing.

Track provenance is documented in assets/music/SOURCE.md.  HandAI's player,
provider mapping and volume handling remain fully offline.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import threading
import wave
from dataclasses import dataclass
from pathlib import Path

from . import audio, preferences


@dataclass(frozen=True)
class Track:
    id: str
    title: str
    screen: str
    filename: str


ALBUM_TITLE = "HANDAI CHIPTUNE SELECT"
TRACKS = (
    Track("main", "Flowerbed Fields", "MAIN MENU", "01-pocket-signal.wav"),
    Track("claude", "Apple Cider", "CLAUDE", "02-copper-constellation.wav"),
    Track("codex", "Pixel Sprinter", "CODEX", "03-green-compile.wav"),
    Track("hermes", "The Cool Factor", "HERMES", "04-wingbeat-relay.wav"),
    Track("opencode", "Heroic Loop", "OPENCODE", "05-blue-brackets.wav"),
    Track("openclaw", "Void Estate", "OPENCLAW", "06-crimson-pincers.wav"),
)
TRACK_BY_ID = {track.id: track for track in TRACKS}


def album_dir() -> Path:
    override = os.environ.get("HANDAI_MUSIC_DIR")
    return Path(override) if override else Path(__file__).parent / "assets" / "music"


def track_path(track: Track | str) -> Path:
    item = TRACK_BY_ID[track] if isinstance(track, str) else track
    return album_dir() / item.filename


def theme_for_provider(provider_id: str) -> str:
    key = str(provider_id).lower()
    for theme in ("claude", "codex", "hermes", "opencode", "openclaw"):
        if key.startswith(theme):
            return theme
    return "main"


def enabled() -> bool:
    return preferences.load().get("music_enabled", True) is not False


def volume() -> int:
    try:
        return max(0, min(100, int(preferences.load().get("music_volume", 35))))
    except (TypeError, ValueError):
        return 35


def save_settings(on: bool, level: int) -> None:
    data = preferences.load()
    data["music_enabled"] = bool(on)
    data["music_volume"] = max(0, min(100, int(level)))
    preferences.save(data)


def scaled_track(track: Track, level: int) -> Path:
    """Create a cached digitally-scaled WAV so every playback backend has volume."""
    source = track_path(track)
    if level >= 100:
        return source
    cache = audio.state_dir().parent / "music-cache"
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / f"{track.id}-{level}.wav"
    if target.exists() and target.stat().st_mtime_ns >= source.stat().st_mtime_ns:
        return target
    with wave.open(str(source), "rb") as incoming:
        params = incoming.getparams()
        if params.sampwidth != 2:
            raise ValueError("CHIPTUNE ASSET MUST BE 16-BIT PCM")
        raw = incoming.readframes(params.nframes)
    gain = max(0, min(100, level)) / 100
    samples = (max(-32768, min(32767, round(sample[0] * gain)))
               for sample in struct.iter_unpack("<h", raw))
    with wave.open(str(target), "wb") as outgoing:
        outgoing.setparams(params)
        outgoing.writeframes(b"".join(struct.pack("<h", sample) for sample in samples))
    return target


def player_argv(path: Path) -> list[str] | None:
    if shutil.which("pw-play"):
        return ["pw-play", str(path)]
    if shutil.which("aplay"):
        return ["aplay", "-q", str(path)]
    if os.name == "nt" and shutil.which("ffplay"):
        return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", str(path)]
    return None


class MusicPlayer:
    """Small looping background player whose requested theme can change safely."""

    def __init__(self):
        self._theme: str | None = None
        self._suspended = False
        self._process: subprocess.Popen[bytes] | None = None
        self._wake = threading.Event()
        self._closed = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def theme(self) -> str | None:
        return self._theme

    def play(self, theme: str) -> None:
        self._theme = theme if theme in TRACK_BY_ID else "main"
        self._wake.set()
        if not self._thread or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="handai-music", daemon=True)
            self._thread.start()

    def refresh(self) -> None:
        self._wake.set()

    def pause(self) -> None:
        self._suspended = True
        self._wake.set()

    def resume(self) -> None:
        self._suspended = False
        self._wake.set()

    def close(self) -> None:
        self._closed.set()
        self._wake.set()
        with self._lock:
            process = self._process
        if process and process.poll() is None:
            process.terminate()
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self) -> None:
        while not self._closed.is_set():
            self._wake.clear()
            theme = self._theme
            if not theme or self._suspended or not enabled() or volume() == 0:
                self._wake.wait(1)
                continue
            try:
                path = scaled_track(TRACK_BY_ID[theme], volume())
            except (OSError, ValueError, wave.Error):
                self._wake.wait(2)
                continue
            argv = player_argv(path)
            if not argv:
                self._wake.wait(2)
                continue
            flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                           stderr=subprocess.DEVNULL, creationflags=flags)
            except OSError:
                self._wake.wait(2)
                continue
            with self._lock:
                self._process = process
            while process.poll() is None and not self._closed.is_set() and not self._wake.wait(0.15):
                pass
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
            with self._lock:
                self._process = None
