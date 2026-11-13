"""Open-palm scrolling. Pure logic; macos.scroll() posts the actual scroll events.

Show an open palm for a moment to grab the page (or open your palm while holding the
pointer: scrolling takes over at once), then move the hand up or down like two fingers on
a trackpad. Lowering the hand, or any other gesture, lets go. Movement is measured at the
middle knuckle, which stays steadier than the fingertips, and lightly smoothed so tremor
doesn't jiggle the page.
"""

from typing import NamedTuple

SCROLL_GESTURE = "Open_Palm"
MIDDLE_MCP = 9           # landmark used for the hand's height
ENTER_S = 0.35           # hold the palm this long before scrolling starts (a palm is a
                         # common resting hand, so a little longer than other gestures)
EXIT_S = 0.4             # palm gone this long: scrolling ends (brief dropouts are fine)
GAIN_PX = 2400.0         # page pixels per full frame height of hand travel
SMOOTHING = 0.5          # 0..1, share of each new reading taken (1 = no smoothing)
DEADZONE = 0.0015        # ignore smaller per-frame moves (fractions of the frame height)


class ScrollStep(NamedTuple):
    active: bool          # your hand is holding the page
    pixels: float = 0.0   # positive: the hand moved down this frame
    entered: bool = False
    left: bool = False


class ScrollMode:
    def __init__(self, enter_s: float = ENTER_S, exit_s: float = EXIT_S,
                 gain_px: float = GAIN_PX, smoothing: float = SMOOTHING,
                 deadzone: float = DEADZONE) -> None:
        self.enter_s, self.exit_s = enter_s, exit_s
        self.gain_px, self.smoothing, self.deadzone = gain_px, smoothing, deadzone
        self.active = False
        self._since: float | None = None
        self._last_seen = 0.0
        self._y = 0.0

    def update(self, gesture: str | None, hand_y: float | None, now: float) -> ScrollStep:
        """gesture: the recognizer's label; hand_y: 0 (top) .. 1 (bottom), None if no hand."""
        palm = gesture == SCROLL_GESTURE and hand_y is not None
        if not self.active:
            return self._waiting(palm, hand_y, now)
        if not palm:
            if now - self._last_seen > self.exit_s:
                self.reset()
                return ScrollStep(active=False, left=True)
            return ScrollStep(active=True)
        self._last_seen = now
        smoothed = self._y + self.smoothing * (hand_y - self._y)
        moved, self._y = smoothed - self._y, smoothed
        if abs(moved) < self.deadzone:
            return ScrollStep(active=True)
        return ScrollStep(active=True, pixels=moved * self.gain_px)

    def _waiting(self, palm: bool, hand_y: float | None, now: float) -> ScrollStep:
        if not palm:
            self._since = None
            return ScrollStep(active=False)
        if self._since is None:
            self._since = now
        if now - self._since < self.enter_s:
            return ScrollStep(active=False)
        self.active, self._last_seen, self._y = True, now, hand_y
        return ScrollStep(active=True, entered=True)

    def start(self, hand_y: float, now: float) -> None:
        """Grab the page now (the open palm already took over from the pointer)."""
        self.active, self._last_seen, self._y, self._since = True, now, hand_y, None

    def reset(self) -> None:
        self.active = False
        self._since = None


def wheel_delta(pixels: float, natural: bool) -> int:
    """Scroll-wheel pixels for a hand movement. With natural scrolling (the macOS
    default) the page follows the fingers, as on a trackpad: hand down, page down."""
    return round(pixels if natural else -pixels)
