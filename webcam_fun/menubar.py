"""Menu bar controller for Desk Guard. Starts desk.py as a child process and
lets you start/stop it and flip its options from the top menu bar."""

import fcntl
import subprocess
import sys
import time

import rumps
from AppKit import NSApplication
from Foundation import NSUserDefaults
from rumps import rumps as rumps_core

from webcam_fun import macos
from webcam_fun import settings as cfg
from webcam_fun.dock import QUIT_ALL_REQUEST, hide_from_dock
from webcam_fun.gestures import __doc__ as GESTURE_HELP
from webcam_fun.recognition import forget_profile
from webcam_fun.settings_window import (
    RELEARN_REQUEST,
    STATUS_NAME,
    TOGGLE_REQUEST,
    open_settings_window,
)
from webcam_fun.vision import ROOT

SETTINGS_FILE = ROOT / "menubar_settings.json"
LOG_FILE = ROOT / "desk.log"
LOCK_FILE = ROOT / ".menubar.lock"
SHOW_REQUEST = ROOT / ".show_request"  # touched by a second launch of the app
CAUGHT_DIR = ROOT / "caught"
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
VIDEO = "Show video window"
ENABLE_ALL, DISABLE_ALL = "Enable all features", "Disable all features"
GROUP_ON, GROUP_OFF = "Enable all gestures", "Disable all gestures"
FEATURES_MENU, GESTURES_MENU, LOCK_MENU = "Features", "Gestures", "Lock after"
SENSITIVITY_TITLE = "Hold time: {level} ({seconds:g}s)"
GUIDE, RELEARN, SETTINGS = "Gesture guide...", "Relearn my face", "Settings..."
CAUGHT, LOG, QUIT = "Open caught photos", "Open log", "Quit Desk Guard"
# Several quick clicks in the menu restart the watcher once, after the last click.
RESTART_DEBOUNCE_S = 0.8
APPLY_TICK_S = 0.25


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
        self.settings = cfg.load(SETTINGS_FILE)
        self.restart_at: float | None = None  # pending restart to apply new settings
        self.settings_mtime = self._settings_mtime()
        self.child: subprocess.Popen | None = None
        self.blocked = False  # camera permission missing; keep retrying
        self.retry_ticks = 0
        self.status = rumps.MenuItem("Stopped")
        self.toggle = rumps.MenuItem(START, callback=self.on_toggle)
        self.switches: dict[str, rumps.MenuItem] = {}
        self.lock_items: dict[int, rumps.MenuItem] = {}
        self.sensitivity_items: dict[str, rumps.MenuItem] = {}
        self.menu = [self.status, self.toggle,
                     rumps.MenuItem(SETTINGS, callback=lambda _: open_settings_window(),
                                    key=","), None,
                     self._switch_item("show_video", VIDEO),
                     rumps.MenuItem(ENABLE_ALL, callback=lambda _: self.set_all(True)),
                     rumps.MenuItem(DISABLE_ALL, callback=lambda _: self.set_all(False)),
                     None, self._features_menu(), self._gestures_menu(), self._lock_menu(),
                     None, rumps.MenuItem(GUIDE, callback=self.on_guide),
                     rumps.MenuItem(RELEARN, callback=self.on_relearn), None,
                     rumps.MenuItem(CAUGHT, callback=self.on_caught),
                     rumps.MenuItem(LOG, callback=self.on_log), None,
                     rumps.MenuItem(QUIT, callback=self.on_quit)]
        self._sync_checkmarks()
        self.timer = rumps.Timer(self.on_poll, POLL_S)
        self.timer.start()
        self.apply_timer = rumps.Timer(self.on_apply_tick, APPLY_TICK_S)
        self.apply_timer.start()
        self.start()

    # ---------- menu building ----------
    def _switch_item(self, key: str, title: str) -> rumps.MenuItem:
        item = rumps.MenuItem(title, callback=lambda _, k=key: self.on_switch(k))
        self.switches[key] = item
        return item

    def _features_menu(self) -> rumps.MenuItem:
        parent = rumps.MenuItem(FEATURES_MENU)
        parent.update([self._switch_item(k, title) for k, title in cfg.FEATURES.items()])
        return parent

    def _gestures_menu(self) -> rumps.MenuItem:
        parent = rumps.MenuItem(GESTURES_MENU)
        on = rumps.MenuItem(GROUP_ON, callback=lambda _: self.set_gestures(True))
        off = rumps.MenuItem(GROUP_OFF, callback=lambda _: self.set_gestures(False))
        switches = [self._switch_item(k, title) for k, title in cfg.GESTURES.items()]
        for level, seconds in cfg.SENSITIVITY.items():
            self.sensitivity_items[level] = rumps.MenuItem(
                SENSITIVITY_TITLE.format(level=level, seconds=seconds),
                callback=lambda _, lv=level: self.on_sensitivity(lv))
        parent.update([on, off, None, switches[0], None, *switches[1:], None,
                       *self.sensitivity_items.values()])
        return parent

    def _lock_menu(self) -> rumps.MenuItem:
        parent = rumps.MenuItem(LOCK_MENU)
        for seconds in cfg.LOCK_CHOICES:
            self.lock_items[seconds] = rumps.MenuItem(
                f"{seconds} seconds", callback=lambda _, s=seconds: self.on_lock_after(s))
        parent.update(list(self.lock_items.values()))
        return parent

    # ---------- child process ----------
    def running(self) -> bool:
        return self.child is not None and self.child.poll() is None

    def command(self) -> list[str]:
        return [sys.executable, "-m", "webcam_fun.desk", *cfg.to_args(self.settings)]

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

    def on_switch(self, key: str) -> None:
        self.change(cfg.toggled(self.settings, key))

    def set_all(self, on: bool) -> None:
        self.change(cfg.with_all(self.settings, on))

    def set_gestures(self, on: bool) -> None:
        self.change(cfg.with_group(self.settings, cfg.GESTURE_SWITCHES, on))

    def on_sensitivity(self, level: str) -> None:
        self.change(self.settings.with_sensitivity(level))

    def on_lock_after(self, seconds: int) -> None:
        self.change(self.settings.with_lock_after(seconds))

    def change(self, new: cfg.Settings) -> None:
        """Save new settings, update the checkmarks, and restart the watcher to apply them."""
        if new == self.settings:
            return
        self.settings = new
        try:
            cfg.save(new, SETTINGS_FILE)
        except OSError as e:
            print(f"could not save settings: {e}", file=sys.stderr)
            macos.popup("Desk Guard", f"Settings not saved: {e}", "#f5a623")
        self.settings_mtime = self._settings_mtime()
        self._applied()

    def _applied(self) -> None:
        self._sync_checkmarks()
        if self.running():  # options are command-line flags, so restart to apply
            self.restart_at = time.monotonic() + RESTART_DEBOUNCE_S

    @staticmethod
    def _settings_mtime() -> float | None:
        try:
            return SETTINGS_FILE.stat().st_mtime
        except OSError:
            return None

    @staticmethod
    def _take_request(name: str) -> bool:
        path = ROOT / name
        if not path.exists():
            return False
        path.unlink(missing_ok=True)
        return True

    def on_apply_tick(self, _) -> None:
        mtime = self._settings_mtime()
        if mtime != self.settings_mtime:  # changed in the settings window
            self.settings_mtime = mtime
            new = cfg.load(SETTINGS_FILE)
            if new != self.settings:
                self.settings = new
                self._applied()
        if self._take_request(TOGGLE_REQUEST):
            self.on_toggle(None)
        if self._take_request(RELEARN_REQUEST):
            self.on_relearn(None)
        if self.restart_at is None or time.monotonic() < self.restart_at:
            return
        self.restart_at = None
        if self.running():
            self.stop()
            self.start()

    def on_guide(self, _) -> None:
        guide = GESTURE_HELP.split("\n\n", 1)[1].rsplit("\nKeys:", 1)[0].strip()
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)  # else it opens behind
        rumps.alert("Desk Guard gestures",
                    f"{guide}\n\nHold time is set under Gestures. Mute needs a longer hold "
                    "so a relaxed hand doesn't mute you.")

    def on_relearn(self, _) -> None:
        """Forget the saved face; the watcher learns it again as soon as it sees one face."""
        was_running = self.running()
        self.stop()
        try:
            forget_profile()
        except OSError as e:
            macos.popup("Desk Guard", f"Could not forget the saved face: {e}", "#f5a623")
            return
        finally:
            if was_running:
                self.start()
        macos.popup("Desk Guard", "Look at the camera on your own for a few seconds so I can "
                    "learn your face.", "#30a46c")

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
        open_settings_window()  # clicking the app while it runs opens its settings

    def _sync_checkmarks(self) -> None:
        flags = self.settings.flags
        for key, item in self.switches.items():
            item.state = flags[key]
        gestures_on = flags["gestures"]
        for key in cfg.GESTURE_SWITCHES[1:]:  # greyed out while gestures are off
            self.switches[key].set_callback(
                (lambda _, k=key: self.on_switch(k)) if gestures_on else None)
        for seconds, item in self.lock_items.items():
            item.state = seconds == self.settings.lock_after
        for level, item in self.sensitivity_items.items():
            item.state = level == self.settings.sensitivity

    def _refresh(self) -> None:
        on = self.running()
        self.title = ICON_ON if on else ICON_OFF
        self.toggle.title = STOP if on else START
        self.status.title = "Watching" if on else "Stopped"
        status = "watching" if on else "camera blocked" if self.blocked else "stopped"
        try:
            (ROOT / STATUS_NAME).write_text(status + "\n")
        except OSError as e:
            print(f"could not write status: {e}", file=sys.stderr)


def main() -> None:
    _lock = single_instance_or_exit()
    QUIT_ALL_REQUEST.unlink(missing_ok=True)  # stale request from an earlier run
    hide_from_dock()
    DeskGuardMenu().run()


if __name__ == "__main__":
    main()
