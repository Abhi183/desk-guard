"""Desk Guard settings window: a native macOS window (WKWebView via pywebview).

    python -m webcam_fun.settings_window

Opened from the gear in the video window, the `s` key, the menu bar, or by clicking
Desk Guard.app while it runs. It edits menubar_settings.json; the menu bar app sees the
change and restarts the watcher. Start/stop and "relearn my face" go to the menu bar app
as request files, because it owns the watcher process.
"""

import fcntl
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from webcam_fun import settings as cfg
from webcam_fun.identity import clean_name
from webcam_fun.vision import ROOT

UI_FILE = Path(__file__).with_name("settings_ui.html")
SETTINGS_NAME = "menubar_settings.json"
STATUS_NAME = ".status"                # written by the menu bar app
TOGGLE_REQUEST = ".toggle_request"     # start / stop watching
RELEARN_REQUEST = ".relearn_request"   # forget the face and learn it again
FOCUS_REQUEST = ".settings_focus"      # a second launch asks this window to come forward
LOCK_NAME = ".settings_window.lock"
STATUSES = ("watching", "stopped", "starting", "camera blocked")
GROUPS = {"features": tuple(cfg.FEATURES), "gestures": cfg.GESTURE_SWITCHES}
WINDOW_SIZE, MIN_SIZE = (920, 640), (780, 540)
FOCUS_POLL_S = 0.4

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
          "detail": "Show the live camera window. Off runs Desk Guard with pop-ups only."},
     ]},
    {"id": "data", "title": "Data & logs", "icon": "folder",
     "subtitle": "Everything stays on this Mac."},
)


def _ok(state: dict) -> dict:
    return {"ok": True, "state": state, "error": None}


def _fail(error: str) -> dict:
    return {"ok": False, "state": None, "error": error}


class SettingsApi:
    """Everything the window can do. Methods are called from JavaScript on worker threads."""

    def __init__(self, root: Path = ROOT,
                 opener: Callable[..., object] = subprocess.Popen) -> None:
        self._root = root
        self._file = root / SETTINGS_NAME
        self._open = opener
        self._lock = threading.Lock()

    # ---------- reading ----------
    def get_state(self) -> dict:
        settings = cfg.load(self._file)
        return {
            "flags": dict(settings.flags),
            "lock_after": settings.lock_after,
            "lock_choices": list(cfg.LOCK_CHOICES),
            "sensitivity": settings.sensitivity,
            "sensitivity_levels": dict(cfg.SENSITIVITY),
            "name": settings.name,
            "status": self._status(),
            "catalog": CATALOG,
        }

    def _status(self) -> str:
        try:
            text = (self._root / STATUS_NAME).read_text().strip()
        except OSError:
            return "stopped"
        return text if text in STATUSES else "stopped"

    # ---------- changing settings ----------
    def _change(self, update: Callable[[cfg.Settings], cfg.Settings]) -> dict:
        with self._lock:
            try:
                new = update(cfg.load(self._file))
                cfg.save(new, self._file)
            except (KeyError, ValueError) as e:
                return _fail(str(e).strip("'\""))
            except OSError as e:
                print(f"settings: could not save: {e}", file=sys.stderr, flush=True)
                return _fail(f"Could not save settings: {e.strerror or e}")
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
        return self._change(lambda s: s.with_name(clean_name(text)))

    # ---------- actions handled by the menu bar app ----------
    def _request(self, name: str) -> dict:
        try:
            (self._root / name).touch()
        except OSError as e:
            return _fail(f"Could not reach Desk Guard: {e.strerror or e}")
        return _ok(self.get_state())

    def toggle_watching(self) -> dict:
        return self._request(TOGGLE_REQUEST)

    def relearn_face(self) -> dict:
        return self._request(RELEARN_REQUEST)

    def open_caught(self) -> dict:
        return self._open_path(self._root / "caught", ["open"])

    def open_snaps(self) -> dict:
        return self._open_path(self._root / "snaps", ["open"])

    def open_log(self) -> dict:
        log = self._root / "desk.log"
        log.touch()
        return self._open_path(log, ["open", "-t"])

    def _open_path(self, path: Path, command: list[str]) -> dict:
        try:
            if command == ["open"]:
                path.mkdir(exist_ok=True)
            self._open([*command, str(path)])
        except OSError as e:
            return _fail(f"Could not open {path.name}: {e.strerror or e}")
        return _ok(self.get_state())


# ---------- the window ----------

def open_settings_window() -> None:
    """Start the window in its own process (it needs the main thread for itself)."""
    subprocess.Popen([sys.executable, "-m", "webcam_fun.settings_window"], cwd=ROOT,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _single_instance():
    handle = open(ROOT / LOCK_NAME, "w")  # noqa: SIM115 - stays open to hold the lock
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        (ROOT / FOCUS_REQUEST).touch()  # bring the open window forward instead
        sys.exit(0)
    return handle


def _bring_forward(window) -> None:
    from AppKit import NSApplication
    from PyObjCTools import AppHelper
    window.restore()
    window.show()
    AppHelper.callAfter(NSApplication.sharedApplication().activateIgnoringOtherApps_, True)


def _watch_focus_requests(window) -> None:
    request = ROOT / FOCUS_REQUEST
    while True:
        time.sleep(FOCUS_POLL_S)
        if request.exists():
            request.unlink(missing_ok=True)
            _bring_forward(window)


def main() -> None:
    import webview

    _lock = _single_instance()
    (ROOT / FOCUS_REQUEST).unlink(missing_ok=True)
    window = webview.create_window(
        "Desk Guard Settings", html=UI_FILE.read_text(), js_api=SettingsApi(),
        width=WINDOW_SIZE[0], height=WINDOW_SIZE[1], min_size=MIN_SIZE,
        background_color="#1c1c1e", vibrancy=True)
    window.events.shown += lambda: _bring_forward(window)
    webview.start(lambda: _watch_focus_requests(window))


if __name__ == "__main__":
    main()
