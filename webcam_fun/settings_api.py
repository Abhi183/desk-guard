"""What the settings window can read and do. Called from the window's JavaScript.

Pure Python with no AppKit, so it is unit tested. The app passes itself in as `host`;
every change is saved to the settings file and handed to the host to apply right away.
"""

import inspect
import sys
from pathlib import Path
from typing import Callable, Protocol

from webcam_fun import settings as cfg
from webcam_fun.identity import clean_name

STATUSES = ("watching", "stopped", "starting", "camera blocked")
GROUPS = {"features": tuple(cfg.FEATURES), "gestures": cfg.GESTURE_SWITCHES}
FOLDERS = {"caught": "caught", "snaps": "snaps"}


class Host(Protocol):
    def status(self) -> str: ...
    def apply_settings(self, settings: cfg.Settings) -> None: ...
    def toggle_watching(self) -> None: ...
    def relearn_face(self) -> None: ...
    def recalibrate_posture(self) -> None: ...


# What the window shows. Sections without items are custom pages drawn by the HTML.
CATALOG = (
    {"id": "overview", "title": "Overview", "icon": "grid"},
    {"id": "security", "title": "Security", "icon": "shield",
     "subtitle": "Protect your screen when you step away or someone is watching.",
     "items": [
         {"key": "auto_lock", "label": "Lock when I leave",
          "detail": "Locks the Mac when you're no longer at the desk, or a stranger sits down."},
         {"key": "guard", "label": "Shoulder-surfer alarm",
          "detail": "Alerts, plays a sound and saves a photo when a second face looks at the screen."},
         {"key": "privacy", "label": "Close personal apps with company",
          "detail": "Quits Messages and WhatsApp and closes Instagram/WhatsApp tabs when "
                    "someone sits with you."},
     ]},
    {"id": "wellbeing", "title": "Wellbeing", "icon": "heart",
     "subtitle": "Gentle nudges while you work.",
     "items": [
         {"key": "posture", "label": "Posture alerts",
          "detail": "Nudges you when you lean in too close or slouch."},
         {"key": "sounds", "label": "Sounds",
          "detail": "Play a sound with alerts. Pop-ups still show when this is off."},
     ]},
    {"id": "gestures", "title": "Gestures", "icon": "hand",
     "subtitle": "Control your Mac with your hand. Only your hand is obeyed.",
     "items": [
         {"key": "gestures", "label": "Gesture control", "master": True,
          "detail": "Turns every gesture below on or off."},
         {"key": "volume", "label": "Volume", "glyph": "👍👎",
          "detail": "Thumbs up / down, hold to keep changing."},
         {"key": "mute", "label": "Mute", "glyph": "✊",
          "detail": "Hold a fist a little longer than other gestures."},
         {"key": "selfie", "label": "Selfie", "glyph": "✌️",
          "detail": "Peace sign saves a photo to snaps/."},
         {"key": "media", "label": "Play / pause", "glyph": "🤟",
          "detail": "Love-you sign plays or pauses YouTube, Spotify, Music."},
         {"key": "tabs", "label": "Switch tabs", "glyph": "👈👉",
          "detail": "Point left or right for the previous / next browser tab."},
         {"key": "cursor", "label": "Move the pointer", "glyph": "☝️",
          "detail": "Point up to take the pointer. Pinch or open palm clicks, fist lifts."},
     ]},
    {"id": "identity", "title": "Face & identity", "icon": "person",
     "subtitle": "Desk Guard learns your face so it only obeys and unlocks for you."},
    {"id": "display", "title": "Display", "icon": "display",
     "subtitle": "How Desk Guard shows itself.",
     "items": [
         {"key": "show_video", "label": "Video window",
          "detail": "Show the live camera window. Off keeps watching with pop-ups only."},
         {"key": "video_on_top", "label": "Keep video on top",
          "detail": "Float the video window above other windows."},
     ]},
    {"id": "data", "title": "Data & logs", "icon": "folder",
     "subtitle": "Everything stays on this Mac."},
)


def _ok(state: dict) -> dict:
    return {"ok": True, "state": state, "error": None}


def _fail(error: str) -> dict:
    return {"ok": False, "state": None, "error": error}


class SettingsApi:
    """Methods the window may call, by name. Everything else is refused."""

    CALLABLE = frozenset({
        "get_state", "set_flag", "set_all", "set_group", "set_lock_after", "set_sensitivity",
        "set_name", "toggle_watching", "relearn_face", "recalibrate_posture", "open_folder",
        "open_log"})

    def __init__(self, settings_file: Path, root: Path, host: Host,
                 opener: Callable[[list[str]], object]) -> None:
        self._file = settings_file
        self._root = root
        self._host = host
        self._open = opener

    def call(self, method: str, args: list) -> dict:
        """Entry point for the JavaScript bridge."""
        if method not in self.CALLABLE or not isinstance(args, list):
            return _fail(f"unknown action: {method}")
        handler = getattr(self, method)
        try:
            inspect.signature(handler).bind(*args)
        except TypeError as e:  # wrong number of arguments from the page
            return _fail(f"bad call to {method}: {e}")
        return handler(*args)

    # ---------- reading ----------
    def get_state(self) -> dict:
        settings = cfg.load(self._file)
        status = self._host.status()
        return {
            "flags": dict(settings.flags),
            "lock_after": settings.lock_after,
            "lock_choices": list(cfg.LOCK_CHOICES),
            "sensitivity": settings.sensitivity,
            "sensitivity_levels": dict(cfg.SENSITIVITY),
            "name": settings.name,
            "status": status if status in STATUSES else "stopped",
            "catalog": CATALOG,
        }

    # ---------- changing settings ----------
    def _change(self, update: Callable[[cfg.Settings], cfg.Settings]) -> dict:
        try:
            new = update(cfg.load(self._file))
        except (KeyError, ValueError) as e:
            return _fail(str(e).strip("'\""))
        try:
            cfg.save(new, self._file)
        except OSError as e:
            print(f"settings: could not save: {e}", file=sys.stderr, flush=True)
            return _fail(f"Could not save settings: {e.strerror or e}")
        self._host.apply_settings(new)
        return _ok(self.get_state())

    def set_flag(self, key: str, on: bool) -> dict:
        if not isinstance(on, bool):
            return _fail("on must be true or false")
        return self._change(lambda s: s.with_flags({key: on}))

    def set_all(self, on: bool) -> dict:
        return self._change(lambda s: cfg.with_all(s, bool(on)))

    def set_group(self, group: str, on: bool) -> dict:
        if group not in GROUPS:
            return _fail(f"unknown group: {group}")
        return self._change(lambda s: cfg.with_group(s, GROUPS[group], bool(on)))

    def set_lock_after(self, seconds: int) -> dict:
        return self._change(lambda s: s.with_lock_after(seconds))

    def set_sensitivity(self, level: str) -> dict:
        return self._change(lambda s: s.with_sensitivity(level))

    def set_name(self, name: str) -> dict:
        text = name.strip() if isinstance(name, str) else ""
        if text and clean_name(text) != text:
            return _fail("Use plain letters, 20 characters at most.")
        return self._change(lambda s: s.with_name(text))

    # ---------- actions ----------
    def toggle_watching(self) -> dict:
        self._host.toggle_watching()
        return _ok(self.get_state())

    def relearn_face(self) -> dict:
        self._host.relearn_face()
        return _ok(self.get_state())

    def recalibrate_posture(self) -> dict:
        self._host.recalibrate_posture()
        return _ok(self.get_state())

    def open_folder(self, name: str) -> dict:
        if name not in FOLDERS:
            return _fail(f"unknown folder: {name}")
        path = self._root / FOLDERS[name]
        try:
            path.mkdir(exist_ok=True)
            self._open(["open", str(path)])
        except OSError as e:
            return _fail(f"Could not open {name}: {e.strerror or e}")
        return _ok(self.get_state())

    def open_log(self) -> dict:
        log = self._root / "desk.log"
        try:
            log.touch()
            self._open(["open", "-t", str(log)])
        except OSError as e:
            return _fail(f"Could not open the log: {e.strerror or e}")
        return _ok(self.get_state())
