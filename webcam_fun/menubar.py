"""Menu bar controller for Desk Guard. Starts desk.py as a child process and
lets you start/stop it and flip its options from the top menu bar."""

import fcntl
import json
import subprocess
import sys

import rumps
from Foundation import NSUserDefaults
from rumps import rumps as rumps_core

from webcam_fun import macos
from webcam_fun.dock import QUIT_ALL_REQUEST, hide_from_dock
from webcam_fun.vision import ROOT

SETTINGS_FILE = ROOT / "menubar_settings.json"
LOG_FILE = ROOT / "desk.log"
LOCK_FILE = ROOT / ".menubar.lock"
SHOW_REQUEST = ROOT / ".show_request"  # touched by a second launch of the app
CAUGHT_DIR = ROOT / "caught"
DEFAULT_SETTINGS = {"show_video": True, "auto_lock": True, "posture": True, "gestures": True}
ICON_ON, ICON_OFF = "👁", "◌"
POLL_S = 2
RETRY_S = 6
CAMERA_ERROR_EXIT = 1  # desk.py exits with 1 when it cannot open the camera
# Where macOS puts the icon, in points from the right edge of the menu bar. Without this a
# full menu bar on a notched MacBook drops new icons behind the notch.
ITEM_AUTOSAVE_NAME = "DeskGuardEye"
ITEM_PREFERRED_POSITION = 260.0
CAMERA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"

START, STOP = "Start watching", "Stop watching"
VIDEO, LOCK, POSTURE = "Show video window", "Lock when I leave", "Posture alerts"
GESTURES = "Gesture control"
CAUGHT, LOG, QUIT = "Open caught photos", "Open log", "Quit Desk Guard"


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_FILE.read_text())
    except (OSError, ValueError):
        saved = {}
    return {k: bool(saved.get(k, v)) for k, v in DEFAULT_SETTINGS.items()}


def save_settings(settings: dict) -> None:
    try:
        SETTINGS_FILE.write_text(json.dumps(settings, indent=2))
    except OSError as e:
        print(f"could not save settings: {e}", file=sys.stderr)


def single_instance_or_exit():
    """Keep one menu bar icon even if the Desktop app is clicked twice."""
    handle = open(LOCK_FILE, "w")  # noqa: SIM115 - stays open to hold the lock
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        SHOW_REQUEST.touch()  # ask the running copy to show itself, then leave
        sys.exit(0)
    return handle  # must stay open for the lock to hold


class _ReopenAwareDelegate(rumps_core.NSApp):
    """rumps' app delegate, plus: clicking the Desktop app while it runs shows a pop-up."""

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        SHOW_REQUEST.touch()
        return True


rumps_core.NSApp = _ReopenAwareDelegate  # rumps instantiates this class inside run()


class DeskGuardMenu(rumps.App):
    def __init__(self) -> None:
        super().__init__("Desk Guard", title=ICON_OFF, quit_button=None)
        self.placed = False
        self.settings = load_settings()
        self.child: subprocess.Popen | None = None
        self.blocked = False  # camera permission missing; keep retrying
        self.retry_ticks = 0
        self.status = rumps.MenuItem("Stopped")
        self.toggle = rumps.MenuItem(START, callback=self.on_toggle)
        self.items = {name: rumps.MenuItem(name, callback=self.on_option)
                      for name in (VIDEO, LOCK, POSTURE, GESTURES)}
        self.menu = [self.status, self.toggle, None, *self.items.values(), None,
                     rumps.MenuItem(CAUGHT, callback=self.on_caught),
                     rumps.MenuItem(LOG, callback=self.on_log), None,
                     rumps.MenuItem(QUIT, callback=self.on_quit)]
        self._sync_checkmarks()
        self.timer = rumps.Timer(self.on_poll, POLL_S)
        self.timer.start()
        self.start()

    # ---------- child process ----------
    def running(self) -> bool:
        return self.child is not None and self.child.poll() is None

    def command(self) -> list[str]:
        cmd = [sys.executable, "-m", "webcam_fun.desk"]
        if not self.settings["show_video"]:
            cmd.append("--no-window")
        if not self.settings["auto_lock"]:
            cmd.append("--no-lock")
        if not self.settings["posture"]:
            cmd.append("--no-posture")
        if not self.settings["gestures"]:
            cmd.append("--no-gestures")
        return cmd

    def start(self) -> None:
        if self.running():
            return
        with open(LOG_FILE, "a") as log:
            self.child = subprocess.Popen(self.command(), cwd=ROOT, stdout=log, stderr=log)
        self._refresh()

    def stop(self) -> None:
        self.blocked, self.retry_ticks = False, 0
        if not self.running():
            return
        self.child.terminate()
        try:
            self.child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.child.kill()
        self._refresh()

    # ---------- menu callbacks ----------
    def on_toggle(self, _) -> None:
        self.stop() if self.running() else self.start()

    def on_option(self, sender: rumps.MenuItem) -> None:
        key = {VIDEO: "show_video", LOCK: "auto_lock", POSTURE: "posture",
               GESTURES: "gestures"}[sender.title]
        self.settings = {**self.settings, key: not self.settings[key]}
        save_settings(self.settings)
        self._sync_checkmarks()
        if self.running():  # options are command-line flags, so restart to apply
            self.stop()
            self.start()

    def on_caught(self, _) -> None:
        CAUGHT_DIR.mkdir(exist_ok=True)
        subprocess.Popen(["open", str(CAUGHT_DIR)])

    def on_log(self, _) -> None:
        LOG_FILE.touch()
        subprocess.Popen(["open", "-t", str(LOG_FILE)])

    def on_quit(self, _) -> None:
        self.stop()
        rumps.quit_application()

    def on_poll(self, _) -> None:
        if QUIT_ALL_REQUEST.exists():  # quit from the Dock or q in the video window
            QUIT_ALL_REQUEST.unlink(missing_ok=True)
            self.on_quit(None)
            return
        self._place_icon()
        self._handle_show_request()
        self._refresh()  # picks up 'q' pressed in the video window, or a crash
        code = None if self.child is None else self.child.poll()
        if code == CAMERA_ERROR_EXIT and not self.blocked:
            self.blocked = True
            self.status.title = "Camera not allowed yet"
            macos.popup("Desk Guard needs the camera",
                        "Turn on Desk Guard under Camera in the window that just opened. "
                        "It starts by itself once allowed.", "#f5a623", 10)
            subprocess.Popen(["open", CAMERA_SETTINGS_URL])
        if not self.blocked:
            return
        if self.running():
            # Survived a full poll after a retry: the camera works now.
            self.blocked, self.retry_ticks = False, 0
            macos.popup("Desk Guard", "Camera allowed. Watching now.", "#30a46c")
            return
        self.retry_ticks += 1
        if self.retry_ticks * POLL_S >= RETRY_S:
            self.retry_ticks = 0
            self.start()

    # ---------- display ----------
    def _place_icon(self) -> None:
        nsapp = getattr(self, "_nsapp", None)  # created by rumps inside run()
        if self.placed or nsapp is None:
            return
        defaults = NSUserDefaults.standardUserDefaults()
        key = f"NSStatusItem Preferred Position {ITEM_AUTOSAVE_NAME}"
        if defaults.objectForKey_(key) is None:  # keep the spot if you cmd-dragged it
            defaults.setObject_forKey_(ITEM_PREFERRED_POSITION, key)
        nsapp.nsstatusitem.setAutosaveName_(ITEM_AUTOSAVE_NAME)
        self.placed = True

    def _handle_show_request(self) -> None:
        if not SHOW_REQUEST.exists():
            return
        SHOW_REQUEST.unlink(missing_ok=True)
        self.start()
        macos.popup("Desk Guard is running",
                    "Options are under the eye icon in the menu bar. Not there? Allow Python in "
                    "System Settings > Menu Bar.",
                    "#30a46c")

    def _sync_checkmarks(self) -> None:
        self.items[VIDEO].state = self.settings["show_video"]
        self.items[LOCK].state = self.settings["auto_lock"]
        self.items[POSTURE].state = self.settings["posture"]
        self.items[GESTURES].state = self.settings["gestures"]

    def _refresh(self) -> None:
        on = self.running()
        self.title = ICON_ON if on else ICON_OFF
        self.toggle.title = STOP if on else START
        self.status.title = "Watching" if on else "Stopped"


def main() -> None:
    _lock = single_instance_or_exit()
    QUIT_ALL_REQUEST.unlink(missing_ok=True)  # stale request from an earlier run
    hide_from_dock()
    DeskGuardMenu().run()


if __name__ == "__main__":
    main()
