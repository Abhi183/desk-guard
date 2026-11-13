"""The watcher, run inside the app on background threads.

Two threads:
  - the camera thread reads frames as fast as the camera delivers them and keeps only the
    newest, so a slow frame never makes the video fall behind;
  - the watcher thread runs DeskApp on the newest frame and, while the video window is
    visible, renders it for the window to pick up.

Settings, key presses and commands are handed over through a lock and applied at the top
of the next frame, so nothing has to restart and no model is reloaded.
"""

import os
import threading
import time
from collections import deque
from typing import Callable

# OpenCV must not ask for camera permission itself: it can only do that on the main thread.
# The app asks through AVFoundation before starting the watcher.
os.environ.setdefault("OPENCV_AVFOUNDATION_SKIP_AUTH", "1")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from webcam_fun import macos, personal  # noqa: E402
from webcam_fun import settings as cfg  # noqa: E402
from webcam_fun.desk import DeskApp, parse_args  # noqa: E402
from webcam_fun.vision import try_open_camera  # noqa: E402

FRAME_WAIT_S = 1.0          # no new frame for this long: the camera is gone (screen locked)
RECONNECT_DELAY_S = 2.0
FPS_REPORT_S = 60.0
RESTART_DELAY_S = 2.0       # after a crash, wait this long before watching again
MAX_CRASHES, CRASH_WINDOW_S = 3, 300.0
GREEN_HEX = "#30a46c"


class LatestFrameCamera:
    """Reads the camera on its own thread and keeps only the newest frame.

    The reading thread also releases the camera, so release() never runs during a read.
    """

    def __init__(self, cap: cv2.VideoCapture) -> None:
        self._cap = cap
        self._cond = threading.Condition()
        self._frame: np.ndarray | None = None
        self._seq = 0
        self._failed = False
        self._running = True
        self._thread = threading.Thread(target=self._loop, name="camera", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        try:
            while self._running:
                ok, frame = self._cap.read()
                with self._cond:
                    if not ok:
                        self._failed = True
                        return
                    self._frame, self._seq = frame, self._seq + 1
                    self._cond.notify_all()
        finally:
            self._cap.release()
            with self._cond:
                self._failed = True
                self._cond.notify_all()

    def read(self, last_seq: int, timeout: float) -> tuple[np.ndarray | None, int]:
        """The newest frame after `last_seq`; (None, last_seq) if none came in time."""
        with self._cond:
            self._cond.wait_for(lambda: self._seq > last_seq or self._failed, timeout)
            if self._seq > last_seq:
                return self._frame, self._seq
            return None, last_seq

    def close(self, timeout: float = 2.0) -> None:
        """Ask the reader to stop; it releases the camera after its current read."""
        self._running = False
        self._thread.join(timeout)


class _Run:
    """One start-to-stop of the watcher. A stopped run never comes back to life."""

    def __init__(self, number: int) -> None:
        self.number = number
        self.stop = threading.Event()


class Watcher:
    """Owns the camera and the DeskApp. All public methods are safe to call from any thread."""

    def __init__(self, settings: cfg.Settings, on_status: Callable[[str], None],
                 camera: int = 0) -> None:
        self._on_status = on_status
        self._camera = camera
        self._lock = threading.Lock()
        self._pending: cfg.Settings | None = settings
        self._latest_settings = settings
        self._keys: deque[int] = deque()
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

    def _watch(self, run: _Run) -> None:
        self._set_status(run, "starting")
        cap = try_open_camera(self._camera)
        if cap is None or not cap.read()[0]:
            if cap is not None:
                cap.release()
            self._set_status(run, "camera blocked")
            return
        camera = LatestFrameCamera(cap)
        app: DeskApp | None = None
        try:
            if run.stop.is_set():
                return
            app = self._make_app()
            if run.stop.is_set():
                return
            self._set_status(run, "watching")
            self._loop(run, app, camera)
        finally:
            if app is not None:
                try:
                    app.close()
                except Exception as e:  # noqa: BLE001 - a broken model must not block stopping
                    log(f"could not close cleanly: {e!r}")
            camera.close()
            if run.stop.is_set():
                self._set_status(run, "stopped")

    def _make_app(self) -> DeskApp:
        with self._lock:
            settings, self._pending = self._pending or self._latest_settings, None
        app = DeskApp(parse_args(cfg.to_args(settings)))
        app.show_gear = False  # the window has a real gear button
        app.apply(settings)
        if settings.flags["privacy"]:
            personal.ask_tab_permission()
        macos.alert("Desk Guard", "Watching",
                    "Sit normally for a moment so I can learn your posture", GREEN_HEX)
        return app

    def _take_inputs(self) -> tuple[cfg.Settings | None, list[int], bool]:
        with self._lock:
            pending, self._pending = self._pending, None
            keys = list(self._keys)
            self._keys.clear()
            return pending, keys, self._render

    def _loop(self, run: _Run, app: DeskApp, camera: LatestFrameCamera) -> None:
        seq, frames, since = 0, 0, time.monotonic()
        while not run.stop.is_set():
            pending, keys, render = self._take_inputs()
            if pending is not None:
                app.apply(pending)
            now = time.monotonic()
            for code in keys:
                app.handle_key(code, now)
            frame, new_seq = camera.read(seq, FRAME_WAIT_S)
            if frame is None:
                camera = self._reconnect(run, camera)
                if camera is None:
                    return
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

    def _reconnect(self, run: _Run, camera: LatestFrameCamera) -> LatestFrameCamera | None:
        """macOS cuts the camera while the screen is locked; wait for it to come back."""
        camera.close()
        log("camera feed lost (screen locked?), waiting for it to come back")
        while not run.stop.wait(RECONNECT_DELAY_S):
            cap = try_open_camera(self._camera)
            if cap is not None and cap.read()[0]:
                log("camera back")
                return LatestFrameCamera(cap)
            if cap is not None:
                cap.release()
        return None


def log(event: str) -> None:
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {event}", flush=True)
