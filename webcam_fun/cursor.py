"""Moving the mouse pointer with your hand. Pure logic; macos.py does the actual moving.

Point your index finger up to take hold of the pointer. It then follows your hand like
a trackpad follows a finger: where the hand starts doesn't matter, only how it moves.
Pinch thumb and index finger to click, make a fist to "lift the mouse" (reposition your
hand without moving the pointer), lower the hand to let go. Open your palm to hand over
to scrolling straight away; hold any other gesture for half a second to let go of the
pointer so that gesture works. While you hold the pointer the other gestures are off.
"""

import math
from typing import NamedTuple

Point = tuple[float, float]
Bounds = tuple[float, float, float, float]  # left, top, right, bottom in screen points

WRIST, THUMB_TIP, INDEX_MCP, INDEX_TIP, MIDDLE_MCP = 0, 4, 5, 8, 9
POINTING_UP, OPEN_PALM, FIST = "Pointing_Up", "Open_Palm", "Closed_Fist"
# Labels that don't take the pointer away: pointing, fist (lift), and frames the
# recognizer couldn't name (common while the hand moves).
POINTER_SHAPES = frozenset({POINTING_UP, FIST, None, "", "None"})
PINCH_ON, PINCH_OFF = 0.3, 0.5  # thumb tip to index tip, in palm lengths
# Screen points per full frame width / height of hand travel at speed multiplier 1. The
# camera frame is 4:3, so a hand movement goes the same distance on screen both ways.
GAIN = (4500.0, 3375.0)
# Pointer acceleration, like a trackpad: multiplier = ACCEL_BASE + ACCEL_SLOPE * hand speed
# (frame widths per second), kept within ACCEL_RANGE. Slow hand = fine control, quick
# sweep = across the screen.
ACCEL_BASE, ACCEL_SLOPE, ACCEL_RANGE = 0.5, 3.0, (0.5, 4.0)
# One Euro filter (Casiez et al. 2012): heavy smoothing while the hand is slow, so tremor
# doesn't shake the pointer, light smoothing while it is fast, so the pointer doesn't lag.
MIN_CUTOFF_HZ, SPEED_COEFF, SPEED_CUTOFF_HZ = 1.0, 8.0, 1.0
MIN_DT_S, MAX_DT_S = 1 / 120, 0.2
EDGE_MARGIN = 1.0   # keep the pointer this far inside the right and bottom edges


def pinch_ratio(pts: list[Point]) -> float | None:
    """Distance between thumb tip and index tip, in palm lengths. None if unusable."""
    if len(pts) < 21:
        return None
    palm = math.dist(pts[WRIST], pts[MIDDLE_MCP])
    if palm < 1e-6:
        return None
    return math.dist(pts[THUMB_TIP], pts[INDEX_TIP]) / palm


class CursorStep(NamedTuple):
    active: bool               # you are holding the pointer
    move: bool = False         # the pointer should follow the hand this frame
    left_click: bool = False
    entered: bool = False      # this frame started cursor mode
    left: bool = False         # this frame ended cursor mode
    lifted: bool = False       # fist: the "mouse" is lifted off the desk
    to_scroll: bool = False    # left because you opened your palm: start scrolling now


class CursorMode:
    """Decides, frame by frame, whether your hand holds the pointer and when it clicks."""

    def __init__(self, enter_s: float = 0.3, exit_s: float = 0.7, handoff_s: float = 0.25,
                 release_s: float = 0.5) -> None:
        self.enter_s = enter_s
        self.exit_s = exit_s
        self.handoff_s = handoff_s    # open palm this long: scrolling takes over
        self.release_s = release_s    # another gesture this long: let go of the pointer
        self.active = False
        self._point_since: float | None = None
        self._last_hand = 0.0
        self._pinched = False
        self._other: str | None = None   # the non-pointer gesture being held
        self._other_since = 0.0

    def update(self, gesture: str | None, pinch: float | None, now: float) -> CursorStep:
        """gesture: the recognizer's label; pinch: pinch_ratio(), None when no hand."""
        if not self.active:
            return self._waiting(gesture, now)
        if pinch is None:
            if now - self._last_hand > self.exit_s:
                self._reset()
                return CursorStep(active=False, left=True)
            return CursorStep(active=True)
        self._last_hand = now
        click = self._update_pinch(pinch)
        # A pinch with the other fingers spread is often labelled an open palm; while the
        # thumb is on the finger it is a pinch, not a palm.
        if gesture == OPEN_PALM and (self._pinched or pinch <= PINCH_OFF):
            gesture = None
        switch = self._switching(gesture, now)
        if switch is not None:
            return switch
        if gesture not in POINTER_SHAPES:  # another gesture is starting: hold still
            return CursorStep(active=True, left_click=click)
        lifted = gesture == FIST and not self._pinched
        return CursorStep(active=True, move=not self._pinched and not lifted,
                          left_click=click, lifted=lifted)

    def _switching(self, gesture: str | None, now: float) -> CursorStep | None:
        """Let go of the pointer when another gesture has been held long enough."""
        if gesture in POINTER_SHAPES:
            self._other = None
            return None
        if gesture != self._other:
            self._other, self._other_since = gesture, now
        held = now - self._other_since
        if gesture == OPEN_PALM and held >= self.handoff_s:
            self._reset()
            return CursorStep(active=False, left=True, to_scroll=True)
        if gesture != OPEN_PALM and held >= self.release_s:
            self._reset()
            return CursorStep(active=False, left=True)
        return None

    def _waiting(self, gesture: str | None, now: float) -> CursorStep:
        if gesture != POINTING_UP:
            self._point_since = None
            return CursorStep(active=False)
        if self._point_since is None:
            self._point_since = now
        if now - self._point_since < self.enter_s:
            return CursorStep(active=False)
        self.active, self._last_hand = True, now
        return CursorStep(active=True, entered=True)

    def _update_pinch(self, pinch: float) -> bool:
        if not self._pinched and pinch < PINCH_ON:
            self._pinched = True
            return True
        if self._pinched and pinch > PINCH_OFF:
            self._pinched = False
        return False

    def _reset(self) -> None:
        self.active = False
        self._point_since = None
        self._pinched = False
        self._other = None


def _alpha(cutoff_hz: float, dt: float) -> float:
    tau = 1.0 / (2 * math.pi * cutoff_hz)
    return 1.0 / (1.0 + tau / dt)


class OneEuroFilter:
    """Speed-adaptive low-pass filter for a 2D point."""

    def __init__(self, min_cutoff: float = MIN_CUTOFF_HZ, speed_coeff: float = SPEED_COEFF,
                 speed_cutoff: float = SPEED_CUTOFF_HZ) -> None:
        self.min_cutoff = min_cutoff
        self.speed_coeff = speed_coeff
        self.speed_cutoff = speed_cutoff
        self.reset((0.0, 0.0))

    def reset(self, point: Point) -> None:
        self.point = point
        self.speed = 0.0  # smoothed speed, in units per second
        self._velocity: Point = (0.0, 0.0)

    def update(self, raw: Point, dt: float) -> Point:
        a = _alpha(self.speed_cutoff, dt)
        self._velocity = tuple(v + a * ((r - p) / dt - v)
                               for v, r, p in zip(self._velocity, raw, self.point, strict=True))
        self.speed = math.hypot(*self._velocity)
        a = _alpha(self.min_cutoff + self.speed_coeff * self.speed, dt)
        self.point = tuple(p + a * (r - p) for r, p in zip(raw, self.point, strict=True))
        return self.point


class CursorDriver:
    """Turns hand movement (fractions of the camera frame) into pointer positions."""

    def __init__(self, gain: Point = GAIN, smoother: OneEuroFilter | None = None,
                 accel: tuple[float, float] = (ACCEL_BASE, ACCEL_SLOPE)) -> None:
        self.gain = gain
        self.accel = accel
        self._filter = smoother or OneEuroFilter()
        self._cursor: Point = (0.0, 0.0)
        self._last = 0.0

    def start(self, hand: Point, cursor: Point, now: float) -> None:
        self._filter.reset(hand)
        self._cursor, self._last = cursor, now

    def sync(self, cursor: Point) -> None:
        """Tell the driver where the pointer really is (the mouse may have moved it)."""
        self._cursor = cursor

    def hold(self, hand: Point, now: float) -> None:
        """Follow the hand without moving the pointer (pinch, open palm, or lifted)."""
        self._filter.reset(hand)
        self._last = now

    def move(self, hand: Point, bounds: Bounds, now: float) -> Point:
        dt = min(max(now - self._last, MIN_DT_S), MAX_DT_S)
        self._last = now
        before = self._filter.point
        after = self._filter.update(hand, dt)
        base, slope = self.accel
        boost = min(max(base + slope * self._filter.speed, ACCEL_RANGE[0]), ACCEL_RANGE[1])
        left, top, right, bottom = bounds
        x = self._cursor[0] + (after[0] - before[0]) * self.gain[0] * boost
        y = self._cursor[1] + (after[1] - before[1]) * self.gain[1] * boost
        self._cursor = (min(max(x, left), right - EDGE_MARGIN),
                        min(max(y, top), bottom - EDGE_MARGIN))
        return self._cursor
