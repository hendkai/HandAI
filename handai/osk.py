"""On-screen keyboard - token/path entry with only a d-pad and two buttons.

The handheld has no letter keys, so any free-text field (API token, working
directory) needs this. Navigation is pure d-pad + A(select)/B(back), which the
curses reference UI and the SDL image share conceptually.

This is the curses reference implementation; the SDL front-end draws the same
grid but reads the gamepad directly.
"""

from __future__ import annotations

import curses

_ROWS = [
    "1234567890",
    "qwertzuiop",
    "asdfghjkl-",
    "yxcvbnm_./",
    "QWERTZUIOP",
    "ASDFGHJKL:",
    "YXCVBNM@+=",
    "~,;?&%#!*",  # extra symbols: paths (~), urls (?&%#), etc.
]
# Special actions live on their own bottom row.
_ACTIONS = ["SPACE", "DEL", "OK", "CANCEL"]


def prompt(stdscr, title: str, initial: str = "", secret: bool = False) -> str | None:
    """Run the OSK modally. Returns the string, or None if cancelled."""
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    buf = list(initial)
    row, col = 0, 0
    on_actions = False
    act = 0

    while True:
        stdscr.erase()
        h, w = stdscr.getmaxyx()
        stdscr.addnstr(0, 0, title, w - 1, curses.A_BOLD)
        shown = ("*" * len(buf)) if secret else "".join(buf)
        stdscr.addnstr(2, 0, "> " + shown + "_", w - 1)

        top = 4
        for r, line in enumerate(_ROWS):
            for c, ch in enumerate(line):
                attr = curses.A_REVERSE if (not on_actions and r == row and c == col) else curses.A_NORMAL
                stdscr.addstr(top + r, 2 + c * 2, ch, attr)
        arow = top + len(_ROWS) + 1
        x = 2
        for i, a in enumerate(_ACTIONS):
            attr = curses.A_REVERSE if (on_actions and i == act) else curses.A_NORMAL
            stdscr.addstr(arow, x, f"[{a}]", attr)
            x += len(a) + 4

        stdscr.addnstr(h - 1, 0, "d-pad move - A select - B backspace - Start OK", w - 1, curses.A_DIM)
        stdscr.refresh()

        k = stdscr.getch()
        if k in (curses.KEY_UP, ord("k")):
            if on_actions:
                on_actions = False
                row = len(_ROWS) - 1
            else:
                row = max(0, row - 1)
        elif k in (curses.KEY_DOWN, ord("j")):
            if not on_actions and row == len(_ROWS) - 1:
                on_actions = True
            elif not on_actions:
                row = min(len(_ROWS) - 1, row + 1)
        elif k in (curses.KEY_LEFT, ord("h")):
            if on_actions:
                act = max(0, act - 1)
            else:
                col = max(0, col - 1)
        elif k in (curses.KEY_RIGHT, ord("l")):
            if on_actions:
                act = min(len(_ACTIONS) - 1, act + 1)
            else:
                col = min(len(_ROWS[row]) - 1, col + 1)
        elif k in (curses.KEY_ENTER, 10, 13):  # A / Enter
            if on_actions:
                a = _ACTIONS[act]
                if a == "SPACE":
                    buf.append(" ")
                elif a == "DEL":
                    if buf:
                        buf.pop()
                elif a == "OK":
                    return "".join(buf)
                elif a == "CANCEL":
                    return None
            else:
                buf.append(_ROWS[row][min(col, len(_ROWS[row]) - 1)])
        elif k in (curses.KEY_BACKSPACE, 127, 8):  # B
            if buf:
                buf.pop()
        elif k == 27:  # Esc / long-B
            return None
        elif k == curses.KEY_F5:  # Start-ish shortcut on many gptk maps -> confirm
            return "".join(buf)
        elif 32 <= k < 127:  # hardware keyboard on dev box
            buf.append(chr(k))
