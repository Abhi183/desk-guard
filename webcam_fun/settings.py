"""Desk Guard's saved options: what's switched on, lock delay, gesture sensitivity, your name.

Pure logic plus a JSON file; the menu bar app edits these and turns them into
command-line flags for desk.py. Settings objects are never changed in place: every
change returns a new one.
"""

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from webcam_fun.identity import clean_name

# key -> menu label. Order is menu order.
FEATURES = MappingProxyType({
    "auto_lock": "Lock when I leave",
    "guard": "Alarm when someone looks over my shoulder",
    "posture": "Posture alerts",
    "privacy": "Close personal apps when someone sits with me",
    "sounds": "Sounds",
})
GESTURES = MappingProxyType({
    "gestures": "Gesture control (only you)",  # master switch for everything below
    "volume": "Thumbs up / down: volume",
    "mute": "Fist: mute",
    "selfie": "Peace sign: selfie",
    "media": "Love-you sign: play / pause",
    "tabs": "Point left / right: switch tabs",
    "cursor": "Point up: move the pointer",
})
GESTURE_SWITCHES = tuple(GESTURES)
ALL_SWITCHES = (*FEATURES, *GESTURES)
# Per-gesture actions that desk.py can turn off with --gestures-off (cursor has its own flag).
GESTURE_ACTIONS = ("volume", "mute", "selfie", "media", "tabs")
OFF_FLAGS = MappingProxyType({"auto_lock": "--no-lock", "guard": "--no-guard",
                              "posture": "--no-posture", "privacy": "--no-privacy",
                              "sounds": "--mute", "gestures": "--no-gestures",
                              "cursor": "--no-cursor", "show_video": "--no-window"})

LOCK_CHOICES = (2, 5, 10, 30)
DEFAULT_LOCK_AFTER = 2
SENSITIVITY = MappingProxyType({"fast": 0.25, "normal": 0.4, "careful": 0.7})  # hold seconds
DEFAULT_SENSITIVITY = "normal"


def _default_flags() -> Mapping[str, bool]:
    return MappingProxyType({"show_video": True, **{key: True for key in ALL_SWITCHES}})


@dataclass(frozen=True)
class Settings:
    flags: Mapping[str, bool] = field(default_factory=_default_flags)
    lock_after: int = DEFAULT_LOCK_AFTER
    sensitivity: str = DEFAULT_SENSITIVITY
    name: str = "you"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Settings):
            return NotImplemented
        return (dict(self.flags), self.lock_after, self.sensitivity, self.name) == \
            (dict(other.flags), other.lock_after, other.sensitivity, other.name)

    def __hash__(self) -> int:
        return hash((tuple(sorted(self.flags.items())), self.lock_after, self.sensitivity,
                     self.name))

    def with_flags(self, changes: Mapping[str, bool]) -> "Settings":
        unknown = set(changes) - set(self.flags)
        if unknown:
            raise KeyError(f"unknown setting: {', '.join(sorted(unknown))}")
        return replace(self, flags=MappingProxyType({**self.flags, **changes}))

    def with_lock_after(self, seconds: int) -> "Settings":
        if seconds not in LOCK_CHOICES:
            raise ValueError(f"lock delay must be one of {LOCK_CHOICES}")
        return replace(self, lock_after=seconds)

    def with_sensitivity(self, level: str) -> "Settings":
        if level not in SENSITIVITY:
            raise ValueError(f"sensitivity must be one of {tuple(SENSITIVITY)}")
        return replace(self, sensitivity=level)


def toggled(settings: Settings, key: str) -> Settings:
    if key not in settings.flags:
        raise KeyError(f"unknown setting: {key}")
    return settings.with_flags({key: not settings.flags[key]})


def with_group(settings: Settings, keys: tuple[str, ...], on: bool) -> Settings:
    return settings.with_flags({key: on for key in keys})


def with_all(settings: Settings, on: bool) -> Settings:
    """Enable all / Disable all. The video window is a view, not a feature, so it stays."""
    return with_group(settings, ALL_SWITCHES, on)


def all_on(settings: Settings, keys: tuple[str, ...] = ALL_SWITCHES) -> bool:
    return all(settings.flags[key] for key in keys)


# ---------- file ----------

def load(path: Path) -> Settings:
    """Settings from the JSON file. Missing or bad entries quietly get their default."""
    try:
        saved = json.loads(path.read_text())
    except (OSError, ValueError):
        return Settings()
    if not isinstance(saved, dict):
        return Settings()
    defaults = Settings()
    flags = {key: saved[key] if isinstance(saved.get(key), bool) else value
             for key, value in defaults.flags.items()}
    lock_after = saved.get("lock_after")
    sensitivity = saved.get("sensitivity")
    return Settings(
        flags=MappingProxyType(flags),
        lock_after=lock_after if lock_after in LOCK_CHOICES else DEFAULT_LOCK_AFTER,
        sensitivity=sensitivity if sensitivity in SENSITIVITY else DEFAULT_SENSITIVITY,
        name=clean_name(saved.get("name")),
    )


def save(settings: Settings, path: Path) -> None:
    """Raises OSError if the file can't be written; the caller decides how to report it."""
    data = {**settings.flags, "lock_after": settings.lock_after,
            "sensitivity": settings.sensitivity, "name": settings.name}
    path.write_text(json.dumps(data, indent=2) + "\n")


# ---------- desk.py command line ----------

def to_args(settings: Settings) -> list[str]:
    """desk.py flags for these settings (everything after `python -m webcam_fun.desk`)."""
    args = ["--name", settings.name, "--lock-after", str(settings.lock_after),
            "--gesture-hold", str(SENSITIVITY[settings.sensitivity])]
    args += [flag for key, flag in OFF_FLAGS.items() if not settings.flags[key]]
    off = [key for key in GESTURE_ACTIONS if not settings.flags[key]]
    if off:
        args += ["--gestures-off", ",".join(off)]
    return args
