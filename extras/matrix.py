#!/usr/bin/env python3
"""Matrix digital rain in the terminal. Press q, Esc or Ctrl+C to quit.

Options:
  --devanagari   only Devanagari characters
  --dense N      drops per column (default 2)
"""

import curses
import locale
import random
import sys
import time

# Standalone Devanagari letters only. Matras and viramas are combining marks
# and would glue onto neighbours, so they are left out.
DEVANAGARI = (
    "अआइईउऊऋएऐओऔ"
    "कखगघङचछजझञटठडढणतथदधनपफबभमयरलवशषसह"
    "ॐ"
    "०१२३४५६७८९"
)
KATAKANA = "ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ"
LATIN = "0123456789ABCDEFZ:.=*+-<>|"
MIXED = DEVANAGARI * 2 + KATAKANA + LATIN  # weight toward Devanagari

FRAME_DELAY = 0.045
MIN_TRAIL, MAX_TRAIL = 10, 40
COLUMN_GAP = 2
DEFAULT_DENSITY = 2
GLITCH_CHANCE = 0.03

# 256-color green ramp from brightest to darkest; fallback uses 8 colors.
GREEN_RAMP_256 = (156, 120, 83, 46, 40, 34, 28, 22)


def parse_args(argv: list) -> tuple:
    chars = DEVANAGARI if "--devanagari" in argv else MIXED
    density = DEFAULT_DENSITY
    if "--dense" in argv:
        idx = argv.index("--dense")
        try:
            density = max(1, int(argv[idx + 1]))
        except (IndexError, ValueError):
            sys.exit("--dense needs a number, e.g. --dense 3")
    return chars, density


def setup_colors() -> tuple:
    """Return (head_attr, [trail attrs from bright to dark])."""
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_WHITE, -1)
    head = curses.color_pair(1) | curses.A_BOLD
    if curses.COLORS >= 256:
        ramp = []
        for i, color in enumerate(GREEN_RAMP_256, start=2):
            curses.init_pair(i, color, -1)
            ramp.append(curses.color_pair(i) | (curses.A_BOLD if i < 6 else 0))
        return head, ramp
    curses.init_pair(2, curses.COLOR_GREEN, -1)
    green = curses.color_pair(2)
    return head, [green | curses.A_BOLD, green | curses.A_BOLD, green, green | curses.A_DIM]


def new_drop(x: int, height: int, start_above: bool = True) -> dict:
    return {
        "x": x,
        "y": random.randint(-height, 0) if start_above else random.randint(0, height),
        "speed": random.choice((1, 1, 1, 2)),
        "trail": random.randint(MIN_TRAIL, MAX_TRAIL),
    }


def put(scr, y: int, x: int, ch: str, attr: int) -> None:
    try:
        scr.addstr(y, x, ch, attr)
    except curses.error:
        pass  # bottom-right cell or out of range mid-resize


def draw_drop(scr, drop: dict, grid: list, height: int, chars: str, colors: tuple) -> None:
    head_attr, ramp = colors
    x, head, trail = drop["x"], drop["y"], drop["trail"]
    for y in range(max(head - trail, 0), min(head + 1, height)):
        dist = head - y
        if dist == 0 or random.random() < GLITCH_CHANCE:
            grid[y][x] = random.choice(chars)
        if dist == 0:
            attr = head_attr
        else:
            attr = ramp[min(dist * len(ramp) // trail, len(ramp) - 1)]
        put(scr, y, x, grid[y][x], attr)
    for y in range(head - trail - drop["speed"], head - trail):
        if 0 <= y < height:
            put(scr, y, x, " ", 0)


def run(scr, chars: str, density: int) -> None:
    curses.curs_set(0)
    scr.nodelay(True)
    colors = setup_colors()

    def reset():
        h, w = scr.getmaxyx()
        scr.clear()
        drops = [
            new_drop(x, h, start_above=False)
            for x in range(0, w - 1, COLUMN_GAP)
            for _ in range(density)
        ]
        return h, w, drops, [[" "] * w for _ in range(h)]

    height, width, drops, grid = reset()
    while True:
        key = scr.getch()
        if key in (ord("q"), ord("Q"), 27):
            return
        if key == curses.KEY_RESIZE:
            height, width, drops, grid = reset()

        for i, drop in enumerate(drops):
            draw_drop(scr, drop, grid, height, chars, colors)
            drop["y"] += drop["speed"]
            if drop["y"] - drop["trail"] > height:
                drops[i] = new_drop(drop["x"], height)

        scr.refresh()
        time.sleep(FRAME_DELAY)


def main() -> None:
    locale.setlocale(locale.LC_ALL, "")  # needed for curses to draw Unicode
    chars, density = parse_args(sys.argv[1:])
    try:
        curses.wrapper(run, chars, density)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
