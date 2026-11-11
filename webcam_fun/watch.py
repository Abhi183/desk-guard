"""Debounced 'someone is behind you' alarm."""

from collections import deque


class ShoulderWatch:
    """Alarms when an extra face shows up in trigger_hits of the last `window` frames.

    The alarm clears only after a full window with no extra face.
    """

    def __init__(self, window: int = 10, trigger_hits: int = 6) -> None:
        self._history: deque[bool] = deque(maxlen=window)
        self._trigger_hits = trigger_hits
        self.alarm = False

    def update(self, extra_face: bool) -> bool:
        """Feed one frame. Returns True only on the frame the alarm starts."""
        self._history.append(extra_face)
        if not self.alarm:
            self.alarm = sum(self._history) >= self._trigger_hits
            return self.alarm
        if not any(self._history):
            self.alarm = False
        return False
