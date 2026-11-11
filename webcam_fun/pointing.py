"""Detects 'pointing left' / 'pointing right' from the 21 hand landmarks.

MediaPipe's built-in gestures only know Pointing_Up, so sideways pointing is worked
out from the landmark geometry: index finger stretched out and roughly horizontal,
the other three fingers curled into the palm. Coordinates are normalized (0..1) in
the mirrored frame, so 'right' is the user's right.
"""

import math

Point = tuple[float, float]

WRIST, INDEX_MCP, INDEX_PIP, INDEX_TIP, MIDDLE_MCP = 0, 5, 6, 8, 9
OTHER_FINGERS = ((10, 12), (14, 16), (18, 20))  # (middle joint, tip) of middle/ring/pinky
INDEX_EXTENDED_RATIO = 1.1   # tip must be this much farther from the wrist than the PIP joint
INDEX_MIN_LENGTH = 0.6       # MCP->tip length as a fraction of palm size
HORIZONTAL_RATIO = 2.0       # |dx| must exceed this many times |dy|


def _dist(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def classify_point(pts: list[Point]) -> str | None:
    """Returns 'Point_Right', 'Point_Left' or None."""
    if len(pts) < 21:
        return None
    wrist = pts[WRIST]
    palm = _dist(wrist, pts[MIDDLE_MCP])
    if palm < 1e-6:
        return None
    if _dist(wrist, pts[INDEX_TIP]) < _dist(wrist, pts[INDEX_PIP]) * INDEX_EXTENDED_RATIO:
        return None
    if _dist(pts[INDEX_MCP], pts[INDEX_TIP]) < INDEX_MIN_LENGTH * palm:
        return None
    for joint, tip in OTHER_FINGERS:
        if _dist(wrist, pts[tip]) > _dist(wrist, pts[joint]):
            return None  # that finger is out too, so it's not a point
    dx = pts[INDEX_TIP][0] - pts[INDEX_MCP][0]
    dy = pts[INDEX_TIP][1] - pts[INDEX_MCP][1]
    if abs(dx) < HORIZONTAL_RATIO * abs(dy):
        return None
    return "Point_Right" if dx > 0 else "Point_Left"
