"""Draws the Desk Guard icon (an eye) as a 1024px PNG. Usage: make_icon.py OUT.png"""

import sys

import cv2
import numpy as np

SIZE = 1024


def draw_icon() -> np.ndarray:
    img = np.zeros((SIZE, SIZE, 4), np.uint8)
    c = SIZE // 2
    # rounded dark tile
    tile = np.zeros((SIZE, SIZE), np.uint8)
    r, pad = 200, 80
    cv2.rectangle(tile, (pad + r, pad), (SIZE - pad - r, SIZE - pad), 255, -1)
    cv2.rectangle(tile, (pad, pad + r), (SIZE - pad, SIZE - pad - r), 255, -1)
    for cx in (pad + r, SIZE - pad - r):
        for cy in (pad + r, SIZE - pad - r):
            cv2.circle(tile, (cx, cy), r, 255, -1, cv2.LINE_AA)
    img[tile > 0] = (40, 30, 24, 255)
    # eye outline: two arcs
    eye = np.zeros((SIZE, SIZE), np.uint8)
    cv2.ellipse(eye, (c, c + 230), (330, 330), 0, 215, 325, 255, -1, cv2.LINE_AA)
    cv2.ellipse(eye, (c, c - 230), (330, 330), 0, 35, 145, 255, -1, cv2.LINE_AA)
    mask = np.zeros_like(eye)
    cv2.ellipse(mask, (c, c), (330, 190), 0, 0, 360, 255, -1, cv2.LINE_AA)
    img[mask > 0] = (240, 240, 240, 255)
    cv2.circle(img, (c, c), 140, (80, 200, 80, 255), -1, cv2.LINE_AA)   # green iris
    cv2.circle(img, (c, c), 62, (20, 20, 20, 255), -1, cv2.LINE_AA)     # pupil
    cv2.circle(img, (c + 45, c - 45), 26, (255, 255, 255, 255), -1, cv2.LINE_AA)
    return img


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    cv2.imwrite(sys.argv[1], draw_icon())
