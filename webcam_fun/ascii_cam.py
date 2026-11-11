"""Your webcam as live ASCII art in the terminal. Ctrl+C to quit.

  --style matrix   green, Matrix-style (default)
  --style color    full color characters
  --style mono     plain characters
"""

import argparse
import shutil
import sys
import time

import cv2
import numpy as np

from webcam_fun.ascii_art import fit_size, to_levels
from webcam_fun.vision import open_camera

RAMP = " .,:;i1tfLCG08@"
GREEN_SHADES = (22, 22, 28, 28, 34, 34, 40, 40, 46, 46, 83, 120, 156, 194, 231)
RESET, HOME, CLEAR = "\x1b[0m", "\x1b[H", "\x1b[2J"
HIDE_CURSOR, SHOW_CURSOR = "\x1b[?25l", "\x1b[?25h"
ALT_SCREEN_ON, ALT_SCREEN_OFF = "\x1b[?1049h", "\x1b[?1049l"
MAX_FPS = 24


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--style", choices=("matrix", "color", "mono"), default="matrix")
    p.add_argument("--invert", action="store_true", help="for light-background terminals")
    p.add_argument("--camera", type=int, default=0)
    return p.parse_args()


MATRIX_TOKENS = np.array([f"\x1b[38;5;{c}m{ch}" for ch, c in zip(RAMP, GREEN_SHADES)])
MONO_TOKENS = np.array(list(RAMP))


def render_frame(frame: np.ndarray, style: str, invert: bool) -> str:
    term = shutil.get_terminal_size((100, 40))
    h, w = frame.shape[:2]
    cols, rows = fit_size(w, h, term.columns, term.lines)
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)  # better contrast in dim rooms
    if invert:
        gray = 255 - gray
    levels = to_levels(gray, cols, rows, len(RAMP))

    if style == "color":
        small = cv2.resize(frame, (cols, rows), interpolation=cv2.INTER_AREA)
        chars = MONO_TOKENS[levels]
        lines = ["".join(f"\x1b[38;2;{r};{g};{b}m{c}" for (b, g, r), c in zip(row_px, row_ch))
                 for row_px, row_ch in zip(small, chars)]
    else:
        tokens = MATRIX_TOKENS if style == "matrix" else MONO_TOKENS
        lines = ["".join(row) for row in tokens[levels]]
    return HOME + (RESET + "\n").join(lines) + RESET


def main() -> None:
    args = parse_args()
    cap = open_camera(args.camera)
    out = sys.stdout
    out.write(ALT_SCREEN_ON + HIDE_CURSOR + CLEAR)
    last_size = shutil.get_terminal_size()
    try:
        while True:
            start = time.monotonic()
            ok, frame = cap.read()
            if not ok:
                break
            size = shutil.get_terminal_size()
            if size != last_size:
                out.write(CLEAR)
                last_size = size
            out.write(render_frame(cv2.flip(frame, 1), args.style, args.invert))
            out.flush()
            time.sleep(max(0.0, 1 / MAX_FPS - (time.monotonic() - start)))
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        out.write(RESET + SHOW_CURSOR + ALT_SCREEN_OFF)
        out.flush()


if __name__ == "__main__":
    main()
