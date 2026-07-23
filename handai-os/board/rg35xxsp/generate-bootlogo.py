#!/usr/bin/env python3
"""Composite HandAI artwork into the pinned vendor's exact BMP container."""

from __future__ import annotations

import struct
import sys
from pathlib import Path


def main() -> int:
    source, destination = map(Path, sys.argv[1:3])
    artwork = (
        Path(sys.argv[3])
        if len(sys.argv) > 3
        else Path(__file__).with_name("assets") / "handai-boot-art-v2.bmp"
    )
    data = bytearray(source.read_bytes())
    if data[:2] != b"BM":
        raise SystemExit("boot logo template is not a BMP")
    offset = struct.unpack_from("<I", data, 10)[0]
    width, height = struct.unpack_from("<ii", data, 18)
    bits = struct.unpack_from("<H", data, 28)[0]
    if (width, abs(height), bits) != (640, 480, 32):
        raise SystemExit(f"unexpected boot logo format: {width}x{height}x{bits}")

    art = artwork.read_bytes()
    if art[:2] != b"BM":
        raise SystemExit("HandAI boot artwork is not a BMP")
    art_offset = struct.unpack_from("<I", art, 10)[0]
    art_width, art_height = struct.unpack_from("<ii", art, 18)
    art_bits = struct.unpack_from("<H", art, 28)[0]
    if (art_width, abs(art_height), art_bits) != (640, 480, 32):
        raise SystemExit(
            f"unexpected HandAI artwork format: {art_width}x{art_height}x{art_bits}"
        )

    repo = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo))
    from handai.pixelgui import _FONT

    top_down = height < 0
    height = abs(height)
    art_top_down = art_height < 0
    art_height = abs(art_height)

    def art_pixel(x: int, y: int) -> tuple[int, int, int]:
        stored_y = y if art_top_down else art_height - 1 - y
        position = art_offset + (stored_y * art_width + x) * 4
        blue, green, red = art[position:position + 3]
        return red, green, blue

    def pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if not (0 <= x < width and 0 <= y < height):
            return
        stored_y = y if top_down else height - 1 - y
        position = offset + (stored_y * width + x) * 4
        red, green, blue = color
        data[position:position + 4] = bytes((blue, green, red, 0xFF))

    def read_pixel(x: int, y: int) -> tuple[int, int, int]:
        stored_y = y if top_down else height - 1 - y
        position = offset + (stored_y * width + x) * 4
        blue, green, red = data[position:position + 3]
        return red, green, blue

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

    cyan = (16, 222, 255)
    pink = (255, 39, 222)
    yellow = (255, 211, 68)
    panel = (4, 6, 22)

    for y in range(height):
        for x in range(width):
            pixel(x, y, art_pixel(x, y))

    # A mostly opaque pixel panel guarantees title readability while preserving
    # the generated network/portal illustration around it.
    for y in range(18, 119):
        for x in range(18, 326):
            current = read_pixel(x, y)
            pixel(
                x,
                y,
                tuple((channel + base * 4) // 5 for channel, base in zip(current, panel)),
            )
    rect(18, 18, 308, 4, cyan)
    rect(18, 115, 308, 4, pink)
    rect(18, 18, 4, 101, cyan)
    rect(322, 18, 4, 101, pink)
    text(40, 35, "HANDAI", yellow, 6)
    text(42, 84, "REMOTE AI COCKPIT", cyan, 2)

    # Static first stage. Once Linux userspace starts, handai.bootdiag replaces
    # this with live milestone updates from the init scripts.
    for y in range(403, 472):
        for x in range(22, 618):
            current = read_pixel(x, y)
            pixel(
                x,
                y,
                tuple((channel + base * 3) // 4 for channel, base in zip(current, panel)),
            )
    text(40, 414, "1/6 BOOTLOADER", cyan, 2)
    rect(40, 447, 560, 20, (127, 132, 178))
    rect(44, 451, 552, 12, (15, 27, 49))
    rect(44, 451, 77, 12, cyan)
    rect(116, 451, 5, 12, pink)
    destination.write_bytes(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
