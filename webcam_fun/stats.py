"""Daily stats: time at the desk, posture, breaks, water, alerts, gestures.

StatsStore keeps one entry per day in stats.json (last KEEP_DAYS days) and is safe to use
from the watcher thread and the main thread at once. DeskTime turns "did I see you this
frame" into desk time and breaks. Both are pure enough to unit test.
"""

import json
import os
import threading
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

KEEP_DAYS = 30
WEEK = 7
COUNTERS = ("posture_alerts", "shoulder_alerts", "locks", "company", "gestures", "scrolls",
            "water_reminders", "water_glasses", "breaks")
SECONDS = ("desk_s", "posture_judged_s", "posture_good_s")
AWAY_GRACE_S = 20.0   # not seen for this long: you've left the desk
BREAK_S = 5 * 60.0    # away at least this long counts as a break
MAX_FRAME_GAP_S = 1.0  # a stalled camera must not add a big chunk of desk time


def _today() -> str:
    return date.today().isoformat()


class StatsStore:
    def __init__(self, path: Path, today: Callable[[], str] = _today) -> None:
        self._path = path
        self._today = today
        self._lock = threading.Lock()
        self._days: dict[str, dict[str, float]] = self._load()
        self._dirty = False

    def _load(self) -> dict[str, dict[str, float]]:
        try:
            raw = json.loads(self._path.read_text())
        except (OSError, ValueError):
            return {}
        if not isinstance(raw, dict):
            return {}
        days = {}
        for day, values in raw.items():
            if not isinstance(values, dict):
                continue
            days[str(day)] = {k: float(v) for k, v in values.items()
                              if k in COUNTERS + SECONDS and isinstance(v, (int, float))
                              and v >= 0}
        return days

    def add(self, key: str, amount: float = 1.0) -> None:
        if key not in COUNTERS + SECONDS:
            raise KeyError(key)
        with self._lock:
            day = self._days.setdefault(self._today(), {})
            day[key] = day.get(key, 0.0) + amount
            self._dirty = True

    def save(self) -> None:
        """Write to disk if anything changed. Raises OSError if it can't be written."""
        with self._lock:
            if not self._dirty:
                return
            keep = sorted(self._days)[-KEEP_DAYS:]
            self._days = {day: self._days[day] for day in keep}
            text = json.dumps(self._days, indent=1, sort_keys=True)
            self._dirty = False
        tmp = self._path.with_suffix(".tmp")
        tmp.write_text(text)
        os.replace(tmp, self._path)  # never leave a half-written file

    def snapshot(self) -> dict:
        """Today's numbers plus the last WEEK days, for the stats page."""
        with self._lock:
            today = self._today()
            days = {day: dict(values) for day, values in self._days.items()}
        current = date.fromisoformat(today)
        week = []
        for back in range(WEEK - 1, -1, -1):
            day = (current - timedelta(days=back)).isoformat()
            values = days.get(day, {})
            week.append({"date": day, "desk_s": values.get("desk_s", 0.0),
                         "good_posture": _good_share(values),
                         "water_glasses": values.get("water_glasses", 0.0)})
        values = days.get(today, {})
        return {"today": {**{k: values.get(k, 0.0) for k in COUNTERS + SECONDS},
                          "good_posture": _good_share(values), "date": today},
                "week": week}


def _good_share(values: dict) -> float | None:
    judged = values.get("posture_judged_s", 0.0)
    return values.get("posture_good_s", 0.0) / judged if judged >= 60 else None


class DeskTime:
    """Whether you're at the desk, from frames where your face was seen."""

    def __init__(self, away_grace_s: float = AWAY_GRACE_S, break_s: float = BREAK_S) -> None:
        self.away_grace_s = away_grace_s
        self.break_s = break_s
        self._last_seen: float | None = None
        self._last_frame: float | None = None

    @property
    def present(self) -> bool:
        return self._present_at(self._last_frame or 0.0)

    def _present_at(self, now: float) -> bool:
        return self._last_seen is not None and now - self._last_seen <= self.away_grace_s

    def update(self, seen: bool, now: float) -> tuple[float, bool]:
        """(seconds at the desk since the last frame, whether you just came back from a
        break)."""
        gap = 0.0 if self._last_frame is None else min(now - self._last_frame, MAX_FRAME_GAP_S)
        was_present = self._present_at(now) if self._last_frame is not None else False
        back_from_break = (seen and self._last_seen is not None
                           and now - self._last_seen >= self.break_s)
        self._last_frame = now
        if seen:
            self._last_seen = now
        desk = gap if (was_present or seen) and self._last_seen is not None else 0.0
        return max(desk, 0.0), back_from_break

