"""Turns a noisy per-frame gesture label into deliberate 'fire' events."""

EPS = 1e-9
NO_GESTURE = (None, "", "None")


class HoldTrigger:
    """Fires when the same gesture is held for hold_s.

    Gestures listed in repeat_s keep firing every repeat_s seconds while held;
    the rest fire once until the hand changes gesture. Frames with no gesture
    shorter than grace_s don't break a hold (the model often drops a frame).
    """

    def __init__(self, hold_s: float = 0.5, repeat_s: dict[str, float] | None = None,
                 grace_s: float = 0.25) -> None:
        self.hold_s = hold_s
        self.repeat_s = dict(repeat_s or {})
        self.grace_s = grace_s
        self._current: str | None = None
        self._since = 0.0
        self._last_seen = 0.0
        self._last_fire: float | None = None

    @property
    def current(self) -> str | None:
        return None if self._current in NO_GESTURE else self._current

    def _reset(self, name: str | None, now: float) -> None:
        self._current, self._since, self._last_seen, self._last_fire = name, now, now, None

    def update(self, name: str | None, now: float) -> str | None:
        real = name not in NO_GESTURE
        holding = self._current not in NO_GESTURE
        if not real and holding and now - self._last_seen <= self.grace_s + EPS:
            return None  # brief dropout: keep the hold going
        if name != self._current:
            self._reset(name, now)
            return None
        if not real:
            return None
        self._last_seen = now
        return self._maybe_fire(name, now)

    def _maybe_fire(self, name: str, now: float) -> str | None:
        if self._last_fire is None:
            if now - self._since + EPS >= self.hold_s:
                self._last_fire = now
                return name
            return None
        interval = self.repeat_s.get(name)
        if interval and now - self._last_fire + EPS >= interval:
            self._last_fire = now
            return name
        return None

    def progress(self, now: float) -> float:
        """0..1 for the on-screen 'charging' bar."""
        if self.current is None:
            return 0.0
        if self._last_fire is not None:
            return 1.0
        return min((now - self._since) / self.hold_s, 1.0)
