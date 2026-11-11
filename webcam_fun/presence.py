"""Decides when to lock the Mac because nobody is at the desk."""


class AutoLock:
    """States: waiting (no face seen yet), armed, counting (face gone), locked."""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = timeout_s
        self.state = "waiting"
        self._gone_since = 0.0

    def update(self, face_present: bool, now: float) -> bool:
        """Feed one frame. Returns True exactly when the screen should be locked."""
        if face_present:
            self.state = "armed"
            return False
        if self.state == "armed":
            self.state, self._gone_since = "counting", now
        if self.state == "counting" and now - self._gone_since >= self.timeout_s:
            self.state = "locked"
            return True
        return False

    def seconds_left(self, now: float) -> float | None:
        if self.state != "counting":
            return None
        return max(self.timeout_s - (now - self._gone_since), 0.0)
