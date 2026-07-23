"""Minimal Linux framebuffer boot/error screen for the RG35XXSP.

This deliberately does not use SDL: it remains available when the accelerated
Mali/SDL path fails and gives hardware testers something better than a black
screen. The H700 BSP exposes a 640x480 RGB565 or BGRA8888 framebuffer.
"""

from __future__ import annotations

import argparse
import mmap
import struct
import sys

from .pixelgui import _FONT


FBIOGET_VSCREENINFO = 0x4600
BOOT_STAGES = ("BOOTLOADER", "KERNEL", "FILESYSTEM", "DRIVERS", "NETWORK", "GUI")
BOOT_STAGE_THRESHOLDS = (0, 20, 35, 55, 75, 90)


def _pixel(color: tuple[int, int, int], bits: int) -> bytes:
    red, green, blue = color
    if bits == 16:
        value = ((red >> 3) << 11) | ((green >> 2) << 5) | (blue >> 3)
        return struct.pack("<H", value)
    if bits == 32:
        return bytes((blue, green, red, 0xFF))
    raise ValueError(f"unsupported framebuffer depth: {bits}")


def _progress_width(total: int, progress: int) -> int:
    return total * max(0, min(100, progress)) // 100


def _stage_index(progress: int) -> int:
    progress = max(0, min(100, progress))
    return max(
        index
        for index, threshold in enumerate(BOOT_STAGE_THRESHOLDS)
        if progress >= threshold
    )


def show(
    message: str,
    device: str = "/dev/fb0",
    *,
    progress: int = 0,
    error: bool = False,
) -> None:
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
            background = _pixel((4, 6, 22), bits)
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

            cyan = (16, 222, 255)
            pink = (255, 39, 222)
            yellow = (255, 211, 68)
            muted = (127, 132, 178)
            red = (255, 72, 82)
            rect(0, 0, width, 8, cyan)
            rect(0, height - 8, width, 8, pink)
            rect(42, 52, 62, 62, cyan)
            rect(56, 66, 34, 34, (4, 6, 22))
            rect(65, 75, 8, 8, yellow)
            rect(78, 75, 8, 8, yellow)
            text(126, 55, "HANDAI", yellow, 5)
            text(128, 99, "PIXEL COCKPIT", cyan, 2)
            text(48, 190, message[:35], red if error else yellow, 3)
            text(48, 250, "BOOT LOG: /DATA/HANDAI/COCKPIT.LOG", muted, 1)

            bar_x, bar_y, bar_w, bar_h = 48, 326, width - 96, 32
            rect(bar_x, bar_y, bar_w, bar_h, muted)
            rect(bar_x + 3, bar_y + 3, bar_w - 6, bar_h - 6, (15, 27, 49))
            fill = _progress_width(bar_w - 6, progress)
            if fill:
                rect(bar_x + 3, bar_y + 3, fill, bar_h - 6, red if error else cyan)
                rect(bar_x + max(3, fill - 5), bar_y + 3, min(5, fill), bar_h - 6, pink)

            stage_index = _stage_index(progress)
            text(
                48,
                377,
                f"{stage_index + 1}/6 {BOOT_STAGES[stage_index]}  {progress:02d}%",
                red if error else cyan,
                2,
            )
            if error:
                text(48, 417, "POWER OFF AND REPORT THIS SCREEN", muted, 1)
            screen.flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="HandAI framebuffer boot progress")
    parser.add_argument("message", nargs="*", default=["STARTING", "HANDAI"])
    parser.add_argument("--progress", type=int, default=0)
    parser.add_argument("--error", action="store_true")
    arguments = parser.parse_args()
    try:
        show(
            " ".join(arguments.message),
            progress=arguments.progress,
            error=arguments.error,
        )
        return 0
    except (OSError, ValueError) as exc:
        print(f"framebuffer diagnostic unavailable: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
