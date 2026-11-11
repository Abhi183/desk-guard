"""Posture check from the face box: leaning in makes the face bigger,
slouching moves it down. Both are compared to a calibrated baseline."""

import statistics

from webcam_fun.vision import Box


class PostureJudge:
    def __init__(self, calib_frames: int = 45, lean_ratio: float = 1.35,
                 drop_frac: float = 0.10, hold_s: float = 5.0,
                 cooldown_s: float = 60.0) -> None:
        self.calib_frames = calib_frames
        self.lean_ratio = lean_ratio
        self.drop_frac = drop_frac
        self.hold_s = hold_s
        self.cooldown_s = cooldown_s
        self.recalibrate()
        self._last_alert = float("-inf")

    def recalibrate(self) -> None:
        self._samples: list[Box] = []
        self._base_area: float | None = None
        self._base_cy: float | None = None
        self._bad_since: float | None = None

    @property
    def calibrated(self) -> bool:
        return self._base_area is not None

    def update(self, box: Box | None, frame_h: int, now: float) -> tuple[str, bool]:
        """Returns (status, should_alert)."""
        if box is None:
            self._bad_since = None
            return ("no_face" if self.calibrated else "calibrating"), False
        if not self.calibrated:
            self._collect(box)
            return "calibrating", False

        status = self._classify(box, frame_h)
        if status == "good":
            self._bad_since = None
            return status, False
        if self._bad_since is None:
            self._bad_since = now
        if now - self._bad_since >= self.hold_s and now - self._last_alert >= self.cooldown_s:
            self._last_alert = now
            return status, True
        return status, False

    def _collect(self, box: Box) -> None:
        self._samples.append(box)
        if len(self._samples) >= self.calib_frames:
            self._base_area = statistics.median(w * h for _, _, w, h in self._samples)
            self._base_cy = statistics.median(y + h / 2 for _, y, _, h in self._samples)

    def _classify(self, box: Box, frame_h: int) -> str:
        _, y, w, h = box
        if w * h / self._base_area > self.lean_ratio:
            return "too_close"
        if (y + h / 2 - self._base_cy) / frame_h > self.drop_frac:
            return "slouching"
        return "good"
