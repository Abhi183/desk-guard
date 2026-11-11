"""Pure helpers for turning a grayscale frame into terminal character levels."""

import cv2
import numpy as np

CHAR_ASPECT = 2.0  # terminal cells are about twice as tall as wide


def fit_size(frame_w: int, frame_h: int, term_cols: int, term_rows: int) -> tuple[int, int]:
    """Largest (cols, rows) that fits the terminal and keeps the frame's shape."""
    rows = max(term_rows - 1, 1)
    cols = round(rows * CHAR_ASPECT * frame_w / frame_h)
    if cols > term_cols:
        cols = term_cols
        rows = max(round(cols * frame_h / (CHAR_ASPECT * frame_w)), 1)
    return max(cols, 1), rows


def to_levels(gray: np.ndarray, cols: int, rows: int, n_levels: int) -> np.ndarray:
    """Downscale and quantize brightness to integers 0..n_levels-1."""
    small = cv2.resize(gray, (cols, rows), interpolation=cv2.INTER_AREA)
    return small.astype(np.int32) * n_levels // 256
