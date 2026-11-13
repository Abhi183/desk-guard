"""Who is who in the frame: which face is you, whose hand that is, and whether
someone is sitting with you. Pure logic, no camera or model code."""

import math

Box = tuple[int, int, int, int]  # x, y, w, h
Point = tuple[float, float]

MAX_NAME_LENGTH = 20
MAX_REACH_FACE_WIDTHS = 6.0   # a hand farther than this from every face belongs to nobody
# Measured: someone sitting alongside is 0.55-0.9 of your face height, someone on a chair
# just behind you about 0.32, people at desks across the room 0.13-0.2. Passers-by can
# reach 0.3 but don't stay for the 8 seconds the countdown needs.
COMPANY_MIN_SIZE_RATIO = 0.25


def clean_name(name: object, default: str = "you") -> str:
    """Your name for the box around your face. The video font only has plain ASCII."""
    if not isinstance(name, str):
        return default
    name = name.strip()
    if not name or len(name) > MAX_NAME_LENGTH or not name.isascii() or not name.isprintable():
        return default
    return name


def pick_me(scores: list[float], threshold: float) -> int | None:
    """Index of the face that matches your profile best, or None if none is close enough."""
    if not scores:
        return None
    best = max(range(len(scores)), key=scores.__getitem__)
    return best if scores[best] >= threshold else None


def hand_owner(wrist: Point, boxes: list[Box],
               max_reach: float = MAX_REACH_FACE_WIDTHS) -> int | None:
    """Index of the face a hand most likely belongs to.

    Distance from the wrist to each face is counted in that face's own widths, so a
    small face at the back of the room doesn't claim a hand held near the camera.
    """
    best, best_reach = None, max_reach
    for i, (x, y, w, h) in enumerate(boxes):
        if w <= 0:
            continue
        reach = math.hypot(wrist[0] - (x + w / 2), wrist[1] - (y + h / 2)) / w
        if reach <= best_reach:
            best, best_reach = i, reach
    return best


def has_company(boxes: list[Box], min_ratio: float = COMPANY_MIN_SIZE_RATIO) -> bool:
    """True when a second face is about as close to the camera as the nearest one."""
    heights = sorted((h for _, _, _, h in boxes), reverse=True)
    return len(heights) > 1 and heights[1] >= heights[0] * min_ratio


class CompanyWatch:
    """Fires once when someone has been sitting with you for hold_s seconds.

    Gaps shorter than grace_s (they turned their head) don't restart the countdown.
    After firing it stays quiet until they have been gone for longer than grace_s.
    """

    def __init__(self, hold_s: float = 8.0, grace_s: float = 2.0) -> None:
        self.hold_s = hold_s
        self.grace_s = grace_s
        self.state = "alone"  # alone, counting, fired
        self._since = 0.0
        self._last_seen = 0.0

    def update(self, company: bool, now: float) -> bool:
        """Feed one frame. Returns True exactly when personal apps should be closed."""
        if company:
            self._last_seen = now
            if self.state == "alone":
                self.state, self._since = "counting", now
        elif self.state != "alone" and now - self._last_seen > self.grace_s:
            self.state = "alone"
        # Only fire while they are in view, not during a gap that may be them leaving.
        if company and self.state == "counting" and now - self._since >= self.hold_s:
            self.state = "fired"
            return True
        return False

    def seconds_left(self, now: float) -> float | None:
        if self.state != "counting":
            return None
        return max(self.hold_s - (now - self._since), 0.0)


class MeTracker:
    """Remembers where your face was for grace_s, for frames where a hand covers it."""

    def __init__(self, grace_s: float = 2.0) -> None:
        self.grace_s = grace_s
        self._box: Box | None = None
        self._seen_at = 0.0

    def update(self, box: Box | None, now: float) -> Box | None:
        """Feed this frame's box for your face (None if not found). Returns the box to use."""
        if box is not None:
            self._box, self._seen_at = box, now
        elif self._box is not None and now - self._seen_at > self.grace_s:
            self._box = None
        return self._box


class OwnerTracker:
    """Whether you are physically at the desk, for auto-lock.

    Your face has to be recognised once. After that you count as present for as long as
    something of you stays in your spot without a break: a face there (even turned away,
    so it no longer matches) or your body (head down on the desk). When the spot has been
    empty for gap_s the chain is broken, and only your recognised face starts a new one,
    so a stranger who sits down in your chair is not taken for you.
    """

    def __init__(self, gap_s: float = 1.5) -> None:
        self.gap_s = gap_s
        self._spot: Box | None = None  # your face box when it was last seen
        self._seen_at = 0.0

    def zone(self, frame_w: int, frame_h: int) -> Box | None:
        """(left, top, right, bottom) of where your head and body should be, or None."""
        if self._spot is None:
            return None
        x, y, w, _ = self._spot
        return (max(x - w, 0), max(y, 0), min(x + 2 * w, frame_w), frame_h)

    def update(self, my_face: Box | None, faces: list[Box], body_in_zone: bool,
               now: float) -> bool:
        """Feed one frame. body_in_zone: a person fills the zone() area."""
        if my_face is not None:
            self._spot, self._seen_at = my_face, now
            return True
        if self._spot is None:
            return False
        face_here = self._face_in_spot(faces)
        if face_here is not None:
            self._spot, self._seen_at = face_here, now  # follow you as you shift about
            return True
        if body_in_zone:
            self._seen_at = now
            return True
        if now - self._seen_at > self.gap_s:
            self._spot = None
        return False

    def _face_in_spot(self, faces: list[Box]) -> Box | None:
        """The face nearest your spot, if it is within one face width of it."""
        x, y, w, h = self._spot
        cx, cy = x + w / 2, y + h / 2
        best, best_distance = None, float(w)
        for face in faces:
            fx, fy, fw, fh = face
            distance = math.hypot(fx + fw / 2 - cx, fy + fh / 2 - cy)
            if distance <= best_distance:
                best, best_distance = face, distance
        return best
