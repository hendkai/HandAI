"""Minimal Linux framebuffer boot/error screen for the RG35XXSP.

This deliberately does not use SDL: it remains available when the accelerated
Mali/SDL path fails and gives hardware testers something better than a black
screen. The H700 BSP exposes a 640x480 RGB565 or BGRA8888 framebuffer.
"""

from __future__ import annotations

import mmap
import struct
import sys

from .pixelgui import _FONT


FBIOGET_VSCREENINFO = 0x4600


def _pixel(color: tuple[int, int, int], bits: int) -> bytes:
    red, green, blue = color
    if bits == 16:
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        return struct.pack("<H", value)
    if bits == 32:
        return bytes((blue, green, red, 0xFF))
    raise ValueError(f"unsupported framebuffer depth: {bits}")


def show(message: str, device: str = "/dev/fb0") -> None:
    import fcntl

    with open(device, "r+b", buffering=0) as framebuffer:
        variable = bytearray(160)
        fcntl.ioctl(framebuffer, FBIOGET_VSCREENINFO, variable, True)
        width, height, virtual_width, virtual_height, _, _, bits = struct.unpack_from(
            "<7I", variable
        )
        bytes_per_pixel = bits // 8
        stride = virtual_width * bytes_per_pixel
        with mmap.mmap(framebuffer.fileno(), stride * virtual_height) as screen:
            background = _pixel((7, 13, 27), bits)
            for y in range(height):
                start = y * stride
                screen[start:start + width * bytes_per_pixel] = background * width

            def rect(x: int, y: int, w: int, h: int, color: tuple[int, int, int]) -> None:
                value = _pixel(color, bits)
                for row in range(max(0, y), min(height, y + h)):
                    left = max(0, x) * bytes_per_pixel
                    right = min(width, x + w) * bytes_per_pixel
                    screen[row * stride + left:row * stride + right] = value * (
                        (right - left) // bytes_per_pixel
                    )

            def text(x: int, y: int, value: str, color: tuple[int, int, int], scale: int) -> None:
                for char in value.upper():
                    glyph = _FONT.get(char, _FONT["?"])
                    for gy, row in enumerate(glyph):
                        for gx, enabled in enumerate(row):
                            if enabled == "1":
                                rect(x + gx * scale, y + gy * scale, scale, scale, color)
                    x += 6 * scale

            cyan = (47, 226, 216)
            pink = (255, 94, 164)
            yellow = (255, 210, 74)
            muted = (144, 161, 184)
            rect(0, 0, width, 8, cyan)
            rect(0, height - 8, width, 8, pink)
            rect(42, 52, 62, 62, cyan)
            rect(56, 66, 34, 34, (7, 13, 27))
            rect(65, 75, 8, 8, yellow)
            rect(78, 75, 8, 8, yellow)
            text(126, 55, "HANDAI", yellow, 5)
            text(128, 99, "PIXEL COCKPIT", cyan, 2)
            text(48, 204, message[:35], yellow, 3)
            text(48, 258, "BOOT LOG: /DATA/HANDAI/COCKPIT.LOG", muted, 1)
            text(48, 286, "POWER OFF, REMOVE SD, REPORT THIS SCREEN", muted, 1)
            screen.flush()


def main() -> int:
    try:
        show(" ".join(sys.argv[1:]) or "STARTING HANDAI")
        return 0
    except (OSError, ValueError) as exc:
        print(f"framebuffer diagnostic unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
