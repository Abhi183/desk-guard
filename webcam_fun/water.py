"""Drink-water reminders. Pure logic: DeskApp feeds it the seconds you spend at the desk.

Only time at the desk counts, so a lunch break doesn't trigger a reminder the moment you
sit back down. If a reminder is ignored it comes back after REMIND_AGAIN_S of desk time;
logging a glass starts the full interval again.
"""

REMIND_AGAIN_S = 15 * 60


class WaterReminder:
    def __init__(self, every_s: float, remind_again_s: float = REMIND_AGAIN_S) -> None:
        self.every_s = every_s
        self.remind_again_s = remind_again_s
        self._due_in = every_s

    def update(self, desk_seconds: float) -> bool:
        """Add time spent at the desk. True when it's time to remind you."""
        self._due_in -= desk_seconds
        if self._due_in > 0:
            return False
        self._due_in = min(self.every_s, self.remind_again_s)
        return True

    def drank(self) -> None:
        self._due_in = self.every_s

    def set_every(self, every_s: float) -> None:
        """New interval; time already counted still counts toward the next reminder."""
        done = self.every_s - self._due_in
        self.every_s = every_s
        self._due_in = max(every_s - done, 0.0)

    @property
    def minutes_left(self) -> float:
        return max(self._due_in, 0.0) / 60
