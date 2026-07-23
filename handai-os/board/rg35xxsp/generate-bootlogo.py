#!/usr/bin/env python3
"""Repaint the pinned vendor BMP as a HandAI-branded 640x480 boot logo."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def main() -> int:
    source, destination = map(Path, sys.argv[1:3])
    data = bytearray(source.read_bytes())
    if data[:2] != b"BM":
        raise SystemExit("boot logo template is not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    if (width, abs(height), bits) != (640, 480, 32):
        raise SystemExit(f"unexpected boot logo format: {width}x{height}x{bits}")

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))
    from handai.pixelgui import _FONT

    top_down = height < 0
    height = abs(height)

    def pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        stored_y = y if top_down else height - 1 - y
        position = offset + (stored_y * width + x) * 4
        red, green, blue = color
        data[position:position + 4] = bytes((blue, green, red, 0xFF))

    def rect(x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
        for row in range(y, y + h):
            for column in range(x, x + w):
                pixel(column, row, color)

    def text(x: int, y: int, value: str, color: tuple[int, int, int], scale: int) -> None:
        for char in value.upper():
            glyph = _FONT.get(char, _FONT["?"])
            for gy, row in enumerate(glyph):
                for gx, enabled in enumerate(row):
                    if enabled == "1":
                        rect(x + gx * scale, y + gy * scale, scale, scale, color)
            x += 6 * scale

    background = (7, 13, 27)
    cyan = (47, 226, 216)
    pink = (255, 94, 164)
    yellow = (255, 210, 74)
    muted = (144, 161, 184)
    rect(0, 0, width, height, background)
    rect(0, 0, width, 8, cyan)
    rect(0, height - 8, width, 8, pink)
    rect(74, 116, 104, 104, cyan)
    rect(94, 136, 64, 64, background)
    rect(108, 150, 14, 14, yellow)
    rect(134, 150, 14, 14, yellow)
    text(214, 122, "HANDAI", yellow, 8)
    text(218, 192, "PIXEL COCKPIT OS", cyan, 3)
    text(160, 326, "BOOTING HANDHELD AI", muted, 3)
    text(218, 382, "RG35XXSP / H700", pink, 2)
    destination.write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
