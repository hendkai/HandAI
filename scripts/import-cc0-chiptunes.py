#!/usr/bin/env python3
"""Rebuild HandAI's curated CC0 soundtrack from its documented sources."""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "handai" / "assets" / "music"
TRACKS = (
    ("https://opengameart.org/sites/default/files/flowerbed_fields.ogg", "01-pocket-signal.wav"),
    ("https://opengameart.org/sites/default/files/apple_cider.ogg", "02-copper-constellation.wav"),
    ("https://opengameart.org/sites/default/files/pixel_sprinter_loop_0.ogg", "03-green-compile.wav"),
    ("https://opengameart.org/sites/default/files/the_cool_factor_loop.wav", "04-wingbeat-relay.wav"),
    ("https://opengameart.org/sites/default/files/heroic_loop_0.wav", "05-blue-brackets.wav"),
    ("https://opengameart.org/sites/default/files/void_estate_0.ogg", "06-crimson-pincers.wav"),
)


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="handai-chiptunes-") as temp:
        source_dir = Path(temp)
        for url, filename in TRACKS:
            source = source_dir / url.rsplit("/", 1)[-1]
            print(f"Downloading {source.name}")
            urllib.request.urlretrieve(url, source)
            subprocess.run(
                [
                    ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-ac", "1", "-ar", "22050",
                    "-sample_fmt", "s16",
                    "-af", "loudnorm=I=-16:LRA=7:TP=-1.5",
                    str(OUTPUT / filename),
                ],
                check=True,
            )
            print(f"Wrote {filename}")


if __name__ == "__main__":
    main()
