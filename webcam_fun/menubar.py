"""Desk Guard, the app: menu bar icon, Dock icon, video window, settings window and the
watcher, all in one process.

    python -m webcam_fun.menubar        (Desk Guard.app runs exactly this)

Settings apply live: nothing restarts when you flip a switch. The watcher runs on
background threads (webcam_fun.engine); everything that touches AppKit runs here on the
main thread.
"""

import fcntl
import subprocess
import sys

import AVFoundation
import objc
import rumps
from AppKit import (
    NSApplication,
    NSApplicationActivationPolicyRegular,
    NSEventModifierFlagCommand,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSMenu,
    NSMenuItem,
)
from Foundation import NSObject, NSUserDefaults
from PyObjCTools import AppHelper
from rumps import rumps as rumps_core

from webcam_fun import macos
from webcam_fun import settings as cfg
from webcam_fun.dock import QUIT_ALL_REQUEST, use_desk_guard_icon
from webcam_fun.engine import Watcher
from webcam_fun.native_toast import Toasts
from webcam_fun.recognition import forget_profile
from webcam_fun.settings_api import SettingsApi
from webcam_fun.settings_panel import SettingsPanel
from webcam_fun.video_window import WATCHER_KEYS, VideoWindow
from webcam_fun.vision import ROOT

SETTINGS_FILE = ROOT / "menubar_settings.json"
LOCK_FILE = ROOT / ".menubar.lock"
SHOW_REQUEST = ROOT / ".show_request"  # a second copy started from Terminal
ICON_ON, ICON_OFF = "👁", "◌"
FRAME_TICK_S = 1 / 30
POLL_S = 1.0
RETRY_S = 6.0
ITEM_AUTOSAVE_NAME = "DeskGuardEye"
ITEM_PREFERRED_POSITION = 260.0
CAMERA_SETTINGS_URL = "x-apple.systempreferences:com.apple.preference.security?Privacy_Camera"
AV_NOT_DETERMINED, AV_AUTHORIZED = 0, 3
AMBER, GREEN = "#f5a623", "#30a46c"
STATUS_LINE = {"watching": "Watching", "starting": "Starting…", "stopped": "Stopped",
               "camera blocked": "Camera not allowed"}
VIDEO_MESSAGE = {"starting": "Starting camera…", "stopped": "Not watching",
                 "camera blocked": "Desk Guard needs the camera. Allow it in System Settings."}

START, STOP = "Start Watching", "Stop Watching"
VIDEO = "Show Video Window"
ENABLE_ALL, DISABLE_ALL = "Enable All Features", "Disable All Features"
SETTINGS, QUIT = "Settings…", "Quit Desk Guard"

_controller = None  # the running DeskGuard, for the app delegate and menu targets


class _AppDelegate(rumps_core.NSApp):
    """rumps' delegate plus: Dock click opens settings, quitting stops the camera."""

    def applicationShouldHandleReopen_hasVisibleWindows_(self, _app, _flag):
        if _controller is not None:
            _controller.open_settings()
        return True

    def applicationWillTerminate_(self, _notification):
        if _controller is not None:
            _controller.shutdown()


rumps_core.NSApp = _AppDelegate  # rumps instantiates this class inside run()


class _MenuTarget(NSObject):
    """Target for the main menu bar items (they need an Objective-C object)."""

    def openSettings_(self, _sender):
        _controller.open_settings()

    def toggleVideo_(self, _sender):
        _controller.set_flag("show_video", not _controller.settings.flags["show_video"])

    def toggleWatching_(self, _sender):
        _controller.toggle_watching()


def _menu_item(title: str, action: str | None, key: str = "", target=None,
               modifiers: int = NSEventModifierFlagCommand) -> NSMenuItem:
    item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(title, action, key)
    item.setKeyEquivalentModifierMask_(modifiers)
    if target is not None:
        item.setTarget_(target)
    return item


def single_instance_or_exit():
    handle = open(LOCK_FILE, "w")  # noqa: SIM115 - stays open to hold the lock
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        SHOW_REQUEST.touch()  # ask the running copy to show its settings, then leave
        sys.exit(0)
    return handle


class DeskGuard(rumps.App):
    def __init__(self) -> None:
        super().__init__("Desk Guard", title=ICON_OFF, quit_button=None)
        self.settings = cfg.load(SETTINGS_FILE)
        self.watcher = Watcher(self.settings, on_status=self._status_from_watcher)
        self.api = SettingsApi(SETTINGS_FILE, ROOT, host=self, opener=subprocess.Popen)
        self.state = "stopped"
        self.user_stopped = False
        self.quitting = False
        self.start_pending = False  # the last run is still shutting down; start when it has
        self.warned_camera = False
        self.retry_in = 0.0
        self.last_seq = 0
        self.placed = False
        self.video: VideoWindow | None = None
        self.panel: SettingsPanel | None = None
        self._target = _MenuTarget.alloc().init()
        self.status_item = rumps.MenuItem(STATUS_LINE["stopped"])
        self.toggle_item = rumps.MenuItem(START, callback=lambda _: self.toggle_watching())
        self.video_item = rumps.MenuItem(VIDEO, callback=lambda _: self.set_flag(
            "show_video", not self.settings.flags["show_video"]))
        self.menu = [self.status_item, self.toggle_item, self.video_item, None,
                     rumps.MenuItem(ENABLE_ALL, callback=lambda _: self.set_all(True)),
                     rumps.MenuItem(DISABLE_ALL, callback=lambda _: self.set_all(False)),
                     rumps.MenuItem(SETTINGS, callback=lambda _: self.open_settings(), key=","),
                     None, rumps.MenuItem(QUIT, callback=lambda _: rumps.quit_application())]
        self.frame_timer = rumps.Timer(self.on_frame_tick, FRAME_TICK_S)
        self.poll_timer = rumps.Timer(self.on_poll, POLL_S)
        rumps.events.before_start.register(self.on_launch)

    # ---------- start-up ----------
    def on_launch(self) -> None:
        app = NSApplication.sharedApplication()
        app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
        use_desk_guard_icon()  # matters when started from Terminal; the .app has its own
        self._build_main_menu(app)
        self.video = VideoWindow(on_settings=self.open_settings,
                                 on_pin=lambda on: self.set_flag("video_on_top", on),
                                 on_close=lambda: self.set_flag("show_video", False),
                                 on_key=self.on_video_key)
        self.panel = SettingsPanel(self.api)
        toasts = Toasts()
        macos.set_popup_handler(lambda *args: AppHelper.callAfter(toasts.show, *args))
        self._sync_ui()
        self.frame_timer.start()
        self.poll_timer.start()
        self.start_watching()
        app.activateIgnoringOtherApps_(True)

    def _build_main_menu(self, app) -> None:
        cmd, opt, shift = (NSEventModifierFlagCommand, NSEventModifierFlagOption,
                           NSEventModifierFlagShift)
        menus = (
            ("Desk Guard", [
                _menu_item("About Desk Guard", "orderFrontStandardAboutPanel:"), None,
                _menu_item(SETTINGS, "openSettings:", ",", self._target), None,
                _menu_item("Hide Desk Guard", "hide:", "h"),
                _menu_item("Hide Others", "hideOtherApplications:", "h", modifiers=cmd | opt),
                _menu_item("Show All", "unhideAllApplications:"), None,
                _menu_item(QUIT, "terminate:", "q")]),
            ("Edit", [
                _menu_item("Undo", "undo:", "z"),
                _menu_item("Redo", "redo:", "z", modifiers=cmd | shift), None,
                _menu_item("Cut", "cut:", "x"), _menu_item("Copy", "copy:", "c"),
                _menu_item("Paste", "paste:", "v"), _menu_item("Select All", "selectAll:", "a")]),
            ("View", [
                _menu_item(VIDEO, "toggleVideo:", "1", self._target),
                _menu_item("Start / Stop Watching", "toggleWatching:", "", self._target)]),
            ("Window", [
                _menu_item("Minimize", "performMiniaturize:", "m"),
                _menu_item("Close", "performClose:", "w"), None,
                _menu_item("Bring All to Front", "arrangeInFront:")]),
        )
        main = NSMenu.alloc().init()
        for title, items in menus:
            menu = NSMenu.alloc().initWithTitle_(title)
            for item in items:
                menu.addItem_(item or NSMenuItem.separatorItem())
            holder = NSMenuItem.alloc().init()
            holder.setSubmenu_(menu)
            main.addItem_(holder)
            if title == "Window":
                app.setWindowsMenu_(menu)
        app.setMainMenu_(main)

    # ---------- watching ----------
    def start_watching(self) -> None:
        """Ask for the camera the macOS way (main thread), then start the watcher."""
        if self.quitting:
            return
        self.user_stopped = False
        av = AVFoundation.AVCaptureDevice
        status = av.authorizationStatusForMediaType_(AVFoundation.AVMediaTypeVideo)
        if status == AV_AUTHORIZED:
            self._start_watcher()
        elif status == AV_NOT_DETERMINED:
            self._show_state("starting")
            av.requestAccessForMediaType_completionHandler_(
                AVFoundation.AVMediaTypeVideo,
                lambda granted: AppHelper.callAfter(self._after_camera_prompt, bool(granted)))
        else:
            self._show_state("camera blocked")

    def _after_camera_prompt(self, granted: bool) -> None:
        if self.quitting or self.user_stopped:
            return
        if granted:
            self._start_watcher()
        else:
            self._show_state("camera blocked")

    def _start_watcher(self) -> None:
        self.start_pending = not self.watcher.start()
        if self.start_pending:
            self._show_state("starting")

    def stop_watching(self) -> None:
        self.user_stopped = True
        self.start_pending = False
        self.watcher.stop()

    def shutdown(self) -> None:
        self.quitting = True
        self.frame_timer.stop()
        self.poll_timer.stop()
        self.watcher.stop()

    def _status_from_watcher(self, status: str) -> None:
        AppHelper.callAfter(self._show_state, status)  # watcher thread -> main thread

    def _show_state(self, status: str) -> None:
        self.state = status
        if status == "camera blocked" and not self.warned_camera:
            self.warned_camera = True
            macos.popup("Desk Guard needs the camera",
                        "Turn on Desk Guard under Camera in the window that just opened. "
                        "It starts by itself once allowed.", AMBER, 10)
            subprocess.Popen(["open", CAMERA_SETTINGS_URL])
        if status == "watching" and self.warned_camera:
            self.warned_camera = False
            macos.popup("Desk Guard", "Camera allowed. Watching now.", GREEN)
        self.retry_in = RETRY_S if status == "camera blocked" else 0.0
        self._sync_ui()

    # ---------- timers ----------
    def on_frame_tick(self, _) -> None:
        if self.video is None or not self.video.visible or self.state != "watching":
            return
        frame, seq = self.watcher.latest(self.last_seq)
        if frame is not None:
            self.last_seq = seq
            self.video.show_frame(frame)

    def on_poll(self, _) -> None:
        if QUIT_ALL_REQUEST.exists():
            QUIT_ALL_REQUEST.unlink(missing_ok=True)
            rumps.quit_application()
            return
        if SHOW_REQUEST.exists():
            SHOW_REQUEST.unlink(missing_ok=True)
            self.open_settings()
        self._place_icon()
        if self.start_pending and not self.user_stopped:
            self._start_watcher()
        if self.state == "camera blocked" and not self.user_stopped:
            self.retry_in -= POLL_S
            if self.retry_in <= 0:
                self.retry_in = RETRY_S
                self.start_watching()

    # ---------- the Host interface used by the settings window ----------
    def status(self) -> str:
        return self.state

    def apply_settings(self, settings: cfg.Settings) -> None:
        self.settings = settings
        self.watcher.apply(settings)
        self._sync_ui()

    def toggle_watching(self) -> None:
        if self.watcher.running or self.start_pending:
            self.stop_watching()
        else:
            self.start_watching()

    def relearn_face(self) -> None:
        if self.watcher.running:
            self.watcher.key(ord("f"))
        else:
            try:
                forget_profile()
            except OSError as e:
                macos.popup("Desk Guard", f"Could not forget the saved face: {e}", AMBER)
                return
        macos.popup("Desk Guard", "Look at the camera on your own for a few seconds so I can "
                    "learn your face.", GREEN)

    def recalibrate_posture(self) -> None:
        self.watcher.key(ord("c"))

    # ---------- changes from the menus and the video window ----------
    def change(self, new: cfg.Settings) -> None:
        if new == self.settings:
            return
        try:
            cfg.save(new, SETTINGS_FILE)
        except OSError as e:
            print(f"could not save settings: {e}", file=sys.stderr, flush=True)
            macos.popup("Desk Guard", f"Settings not saved: {e.strerror or e}", AMBER)
        self.apply_settings(new)

    def set_flag(self, key: str, on: bool) -> None:
        self.change(self.settings.with_flags({key: on}))

    def set_all(self, on: bool) -> None:
        self.change(cfg.with_all(self.settings, on))

    def open_settings(self) -> None:
        if self.panel is None:
            return
        NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
        self.panel.show()

    def on_video_key(self, char: str) -> bool:
        if char == "s":
            self.open_settings()
            return True
        if char in WATCHER_KEYS:
            self.watcher.key(ord(char))
            return True
        return False

    # ---------- display ----------
    def _sync_ui(self) -> None:
        flags = self.settings.flags
        on = self.state == "watching"
        self.title = ICON_ON if on else ICON_OFF
        self.status_item.title = STATUS_LINE.get(self.state, self.state)
        self.toggle_item.title = STOP if self.watcher.running else START
        self.video_item.state = flags["show_video"]
        if self.video is not None:
            self.video.set_on_top(flags["video_on_top"])
            self.video.set_message(None if on else VIDEO_MESSAGE.get(self.state))
            if flags["show_video"] and not self.video.visible:
                self.last_seq = 0
                self.video.show()
            elif not flags["show_video"] and self.video.visible:
                self.video.hide()
        if self.panel is not None:
            self.panel.push_state()

    def _place_icon(self) -> None:
        nsapp = getattr(self, "_nsapp", None)
        if self.placed or nsapp is None:
            return
        defaults = NSUserDefaults.standardUserDefaults()
        key = f"NSStatusItem Preferred Position {ITEM_AUTOSAVE_NAME}"
        if defaults.objectForKey_(key) is None:
            defaults.setObject_forKey_(ITEM_PREFERRED_POSITION, key)
        nsapp.nsstatusitem.setAutosaveName_(ITEM_AUTOSAVE_NAME)
        self.placed = True


def main() -> None:
    global _controller
    _lock = single_instance_or_exit()
    QUIT_ALL_REQUEST.unlink(missing_ok=True)
    objc.options.verbose = True  # print Objective-C exceptions from callbacks to the log
    _controller = DeskGuard()
    _controller.run()


if __name__ == "__main__":
    main()
