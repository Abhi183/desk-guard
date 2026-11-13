"""The watcher, run inside the app on background threads.

Two threads:
  - AVFoundation's capture queue delivers frames from the camera you picked (by its ID,
    see avcamera.py) and keeps only the newest, so the video never falls behind;
  - the watcher thread runs DeskApp on the newest frame and, while the video window is
    visible, renders it for the window to pick up.

Settings, key presses and commands are handed over through a lock and applied at the top
of the next frame, so nothing has to restart and no model is reloaded.
"""

import threading
import time
from collections import deque
from typing import Callable

import cv2
import numpy as np

from webcam_fun import macos, personal
from webcam_fun import settings as cfg
from webcam_fun.avcamera import AVCamera
from webcam_fun.cameras import Camera, choose, list_cameras
from webcam_fun.desk import DeskApp, parse_args
from webcam_fun.stats import StatsStore

FRAME_WAIT_S = 1.0          # no new frame for this long: the camera is gone (screen locked)
RECONNECT_DELAY_S = 2.0
FPS_REPORT_S = 60.0
STATS_SAVE_S = 60.0
COMMANDS = {"water": "drank_water"}  # commands from the app -> DeskApp methods
CAMERA_CHECK_S = 2.0
FAILED_CAMERA_WAIT_S = 10.0  # a camera that wouldn't start isn't tried again before this        # how often to look for a better camera (plugged in, or picked)
RESTART_DELAY_S = 2.0       # after a crash, wait this long before watching again
MAX_CRASHES, CRASH_WINDOW_S = 3, 300.0
GREEN_HEX = "#30a46c"


class _Feed:
    """The camera one run is watching with."""

    def __init__(self, opener: Callable[[], AVCamera] = AVCamera) -> None:
        self._opener = opener
        self.reader: AVCamera | None = None
        self.camera: Camera | None = None

    def open(self, camera: Camera) -> bool:
        """Start a session on `camera`. False if it gives no picture."""
        reader = self._opener()
        if not reader.open(camera.uid):
            reader.close()
            return False
        self.reader, self.camera = reader, camera
        return True

    def switch(self, camera: Camera) -> bool:
        """Swap the running session over to `camera` (fast). False keeps the old one."""
        if self.reader is None or not self.reader.switch_to(camera.uid):
            return False
        self.camera = camera
        return True

    def close(self) -> None:
        if self.reader is not None:
            self.reader.close()
        self.reader = None


class _Run:
    """One start-to-stop of the watcher. A stopped run never comes back to life."""

    def __init__(self, number: int) -> None:
        self.number = number
        self.stop = threading.Event()


class Watcher:
    """Owns the camera and the DeskApp. All public methods are safe to call from any thread."""

    def __init__(self, settings: cfg.Settings, on_status: Callable[[str], None],
                 on_camera: Callable[[], None] = lambda: None,
                 stats: StatsStore | None = None) -> None:
        self._stats = stats
        self._on_status = on_status
        self._on_camera = on_camera  # the camera in use changed
        self.in_use: Camera | None = None
        self._failed: tuple[str, float] | None = None  # (camera ID, when it failed)
        self._lock = threading.Lock()
        self._pending: cfg.Settings | None = settings
        self._latest_settings = settings
        self._keys: deque[int | str] = deque()  # key codes, or command names
        self._render = settings.flags["show_video"]
        self._out: np.ndarray | None = None
        self._out_seq = 0
        self._thread: threading.Thread | None = None
        self._run: _Run | None = None
        self._runs = 0
        self.status = "stopped"

    # ---------- control ----------
    def start(self) -> bool:
        """False if the previous run is still shutting down (try again shortly)."""
        if self._thread is not None and self._thread.is_alive():
            return self._run is not None and not self._run.stop.is_set()
        self._runs += 1
        run = _Run(self._runs)
        with self._lock:
            self._pending = self._latest_settings  # a fresh DeskApp needs every setting
        self._run = run
        self._thread = threading.Thread(target=self._main, args=(run,), name="watcher",
                                        daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 3.0) -> None:
        run = self._run
        if run is not None:
            run.stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if not self._thread.is_alive():
                self._thread = None
        self._set_status(run, "stopped")

    @property
    def running(self) -> bool:
        """Watching, or about to: a run that hasn't been told to stop."""
        return (self._thread is not None and self._thread.is_alive()
                and self._run is not None and not self._run.stop.is_set())

    def apply(self, settings: cfg.Settings) -> None:
        with self._lock:
            self._pending = settings
            self._latest_settings = settings
            self._render = settings.flags["show_video"]

    def key(self, code: int) -> None:
        with self._lock:
            self._keys.append(code)

    def command(self, name: str) -> None:
        """Run a DeskApp command (see COMMANDS) on the watcher thread."""
        if name not in COMMANDS:
            raise KeyError(name)
        with self._lock:
            self._keys.append(name)

    def latest(self, after_seq: int) -> tuple[np.ndarray | None, int]:
        """The newest rendered frame if there is one newer than `after_seq`."""
        with self._lock:
            if self._out_seq > after_seq:
                return self._out, self._out_seq
            return None, after_seq

    # ---------- the watcher thread ----------
    def _set_status(self, run: _Run | None, status: str) -> None:
        if run is not self._run:
            return  # an old run finishing late must not overwrite the current state
        if status != self.status:
            self.status = status
            self._on_status(status)

    def _main(self, run: _Run) -> None:
        """Runs the watcher, restarting it after a crash (a few times, then gives up)."""
        crashes: deque[float] = deque()
        while not run.stop.is_set():
            try:
                self._watch(run)
                return  # stopped, or the camera isn't available
            except Exception as e:  # noqa: BLE001 - log it and keep the app protecting you
                now = time.monotonic()
                log(f"watcher crashed: {e!r}")
                crashes.append(now)
                while crashes and now - crashes[0] > CRASH_WINDOW_S:
                    crashes.popleft()
                if len(crashes) > MAX_CRASHES:
                    log(f"watcher crashed {len(crashes)} times in a few minutes; giving up")
                    self._set_status(run, "stopped")
                    return
                self._set_status(run, "starting")
                if run.stop.wait(RESTART_DELAY_S):
                    return

    def _wanted(self) -> str:
        with self._lock:
            return self._latest_settings.camera

    def _use(self, feed: _Feed) -> None:
        self.in_use = feed.camera
        self._on_camera()

    def _watch(self, run: _Run) -> None:
        self._set_status(run, "starting")
        feed = _Feed()
        pick = self._wait_for_a_camera(run)
        if pick is None:
            return  # stopped while waiting
        if not feed.open(pick):
            self._set_status(run, "camera blocked")
            return
        log(f"watching with {pick.name}")
        self._use(feed)
        app: DeskApp | None = None
        try:
            if run.stop.is_set():
                return
            app = self._make_app()
            if run.stop.is_set():
                return
            self._set_status(run, "watching")
            self._loop(run, app, feed)
        finally:
            if app is not None:
                try:
                    app.close()
                except Exception as e:  # noqa: BLE001 - a broken model must not block stopping
                    log(f"could not close cleanly: {e!r}")
            feed.close()
            self.in_use = None
            self._save_stats()
            if run.stop.is_set():
                self._set_status(run, "stopped")

    def _wait_for_a_camera(self, run: _Run) -> Camera | None:
        """The camera to use, waiting (status "no camera") while none is connected."""
        while not run.stop.is_set():
            pick = choose(list_cameras(), self._wanted())
            if pick is not None:
                return pick
            self._set_status(run, "no camera")
            run.stop.wait(RECONNECT_DELAY_S)
        return None

    def _make_app(self) -> DeskApp:
        with self._lock:
            settings, self._pending = self._pending or self._latest_settings, None
        app = DeskApp(parse_args(cfg.to_args(settings)))
        app.show_gear = False  # the window has a real gear button
        app.stats = self._stats
        app.apply(settings)
        if settings.flags["privacy"]:
            personal.ask_tab_permission()
        macos.alert("Desk Guard", "Watching",
                    "Sit normally for a moment so I can learn your posture", GREEN_HEX)
        return app

    def _save_stats(self) -> None:
        if self._stats is None:
            return
        try:
            self._stats.save()
        except OSError as e:
            log(f"could not save stats: {e}")

    def _take_inputs(self) -> tuple[cfg.Settings | None, list[int | str], bool]:
        with self._lock:
            pending, self._pending = self._pending, None
            keys = list(self._keys)
            self._keys.clear()
            return pending, keys, self._render

    def _loop(self, run: _Run, app: DeskApp, feed: _Feed) -> None:
        seq, frames, since = 0, 0, time.monotonic()
        next_check = time.monotonic() + CAMERA_CHECK_S
        next_save = time.monotonic() + STATS_SAVE_S
        while not run.stop.is_set():
            pending, keys, render = self._take_inputs()
            if pending is not None:
                app.apply(pending)
                next_check = 0.0  # the camera choice may have changed: look now
            now = time.monotonic()
            for code in keys:
                if isinstance(code, str):
                    getattr(app, COMMANDS[code])()
                else:
                    app.handle_key(code, now)
            if now >= next_save:
                next_save = now + STATS_SAVE_S
                self._save_stats()
            if now >= next_check:
                next_check = now + CAMERA_CHECK_S
                if self._switch_if_better(feed):
                    app.camera_changed()
                    seq = 0
            frame, new_seq = feed.reader.read(seq, FRAME_WAIT_S)
            if frame is None:
                before = feed.camera
                if not self._reconnect(run, feed):
                    return
                if feed.camera != before:
                    app.camera_changed()
                seq = 0
                continue
            seq = new_seq
            frame = cv2.flip(frame, 1)
            now = time.monotonic()
            faces = app.step(frame, now)
            if render:
                out = app.render(frame, faces, now)
                with self._lock:
                    self._out, self._out_seq = out, self._out_seq + 1
            frames += 1
            if now - since >= FPS_REPORT_S:
                log(f"watching at {frames / (now - since):.0f} fps")
                frames, since = 0, now

    def _switch_if_better(self, feed: _Feed) -> bool:
        """Move to the camera you picked (or the automatic choice) if it isn't the one in
        use: it was just plugged back in, or you changed the setting. True if switched."""
        best = choose(list_cameras(), self._wanted())
        if best is None or feed.camera is None or best.uid == feed.camera.uid:
            return False
        if self._failed is not None and self._failed[0] == best.uid and \
                time.monotonic() - self._failed[1] < FAILED_CAMERA_WAIT_S:
            return False
        old = feed.camera
        started = time.monotonic()
        if feed.switch(best):
            log(f"switched camera: {old.name} -> {best.name} "
                f"({time.monotonic() - started:.1f} s)")
            self._use(feed)
            return True
        log(f"could not switch to {best.name}; staying with {old.name}")
        self._failed = (best.uid, time.monotonic())
        return False

    def _reconnect(self, run: _Run, feed: _Feed) -> bool:
        """The picture stopped: the camera was unplugged, or macOS paused it while the
        screen is locked. Use the best camera available as soon as one gives a picture."""
        lost = feed.camera.name if feed.camera else "camera"
        feed.close()
        log(f"{lost} stopped (unplugged, or screen locked); looking for a camera")
        while not run.stop.wait(RECONNECT_DELAY_S):
            cameras = list_cameras()
            if not cameras:
                self._set_status(run, "no camera")
                continue
            pick = choose(cameras, self._wanted())
            if pick is not None and feed.open(pick):
                log(f"watching with {pick.name}")
                self._use(feed)
                self._set_status(run, "watching")
                return True
        return False


def log(event: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event}", flush=True)
