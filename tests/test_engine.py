import argparse
import threading
import time

import numpy as np

from webcam_fun.engine import LatestFrameCamera
from webcam_fun.gestures import GestureApp


class FakeCapture:
    """Delivers numbered frames, then fails (like a camera cut off by the lock screen)."""

    def __init__(self, frames: int, delay_s: float = 0.0):
        self.left = frames
        self.delay_s = delay_s
        self.count = 0
        self.released = False
        self.gate = threading.Event()
        self.gate.set()

    def read(self):
        self.gate.wait()
        time.sleep(self.delay_s)
        if self.left == 0:
            return False, None
        self.left -= 1
        self.count += 1
        return True, np.full((2, 2, 3), self.count, np.uint8)

    def release(self):
        self.released = True


def test_reader_skips_to_the_newest_frame():
    cap = FakeCapture(frames=5)
    camera = LatestFrameCamera(cap)
    time.sleep(0.1)  # all five frames arrive before anyone reads
    frame, seq = camera.read(0, timeout=1)
    assert seq == 5 and frame[0, 0, 0] == 5  # frames 1-4 were dropped, not queued
    camera.close()
    assert cap.released


def test_read_times_out_without_a_new_frame():
    cap = FakeCapture(frames=1)
    cap.gate.clear()  # camera stalls
    camera = LatestFrameCamera(cap)
    start = time.monotonic()
    frame, seq = camera.read(0, timeout=0.2)
    assert frame is None and seq == 0
    assert time.monotonic() - start >= 0.19
    cap.gate.set()
    camera.close()


def test_failed_camera_wakes_the_reader_at_once():
    cap = FakeCapture(frames=0)
    camera = LatestFrameCamera(cap)
    start = time.monotonic()
    frame, _ = camera.read(0, timeout=5)
    assert frame is None
    assert time.monotonic() - start < 1  # didn't sit out the whole timeout
    camera.close()


def _gesture_app():
    return GestureApp(argparse.Namespace(no_keys=False, no_cursor=False, gestures_off="",
                                         gesture_hold=0.4))


def test_configure_switches_gestures_live():
    app = _gesture_app()
    app.configure(disabled={"mute", "tabs"}, cursor=True, hold=0.4)
    assert app.disabled == frozenset({"mute", "tabs"})


def test_configure_new_hold_time_rebuilds_the_trigger():
    app = _gesture_app()
    before = app.trigger
    app.configure(disabled=set(), cursor=True, hold=0.4)
    assert app.trigger is before  # same hold: keep a hold that is in progress
    app.configure(disabled=set(), cursor=True, hold=0.7)
    assert app.trigger is not before and app.trigger.hold_s == 0.7


def test_switching_the_pointer_off_lets_go_of_it():
    app = _gesture_app()
    app.cursor_mode.active = True
    app.configure(disabled=set(), cursor=False, hold=0.4)
    assert not app.cursor_mode.active and not app.cursor_enabled


# ---------- Watcher lifecycle ----------

from webcam_fun import engine  # noqa: E402
from webcam_fun import settings as cfg  # noqa: E402


FACETIME = engine.Camera("builtin-1", "FaceTime HD Camera", True, 0)


def _watcher(monkeypatch, statuses, camera=None, cameras=(FACETIME,)):
    monkeypatch.setattr(engine, "try_open_camera", lambda _index: camera)
    monkeypatch.setattr(engine, "list_cameras", lambda: list(cameras))
    return engine.Watcher(cfg.Settings(), on_status=statuses.append)


def test_camera_that_gives_no_picture_reports_blocked_and_ends(monkeypatch):
    statuses = []
    w = _watcher(monkeypatch, statuses)
    assert w.start()
    w._thread.join(2)
    assert statuses == ["starting", "camera blocked"]
    assert not w.running


def test_no_camera_connected_waits_instead_of_failing(monkeypatch):
    statuses = []
    w = _watcher(monkeypatch, statuses, cameras=())
    assert w.start()
    time.sleep(0.2)
    assert statuses == ["starting", "no camera"] and w.running  # still waiting for one
    w.stop()
    assert statuses[-1] == "stopped" and not w.running


def test_feed_opens_the_camera_by_its_current_number(monkeypatch):
    opened = []
    monkeypatch.setattr(engine, "try_open_camera",
                        lambda index: opened.append(index) or FakeCapture(frames=3))
    feed = engine._Feed()
    usb = engine.Camera("usb-2", "Desk Camera", False, 1)
    assert feed.open(usb) and feed.camera == usb and opened == [1]
    feed.close()
    assert feed.reader is None


def test_a_finished_old_run_cannot_change_the_status(monkeypatch):
    statuses = []
    w = _watcher(monkeypatch, statuses)
    old, new = engine._Run(1), engine._Run(2)
    w._run = new
    w._set_status(old, "watching")
    assert statuses == [] and w.status == "stopped"


def test_start_refuses_while_the_last_run_is_still_stopping(monkeypatch):
    w = _watcher(monkeypatch, [])
    gate = threading.Event()
    w._run = engine._Run(1)
    w._run.stop.set()
    w._thread = threading.Thread(target=gate.wait)  # stands in for a slow shutdown
    w._thread.start()
    assert w.start() is False and not w.running
    gate.set()
    w._thread.join()
    assert w.start() is True


def test_camera_is_released_by_the_reading_thread():
    cap = FakeCapture(frames=10_000, delay_s=0.01)
    released_on = []
    cap.release = lambda: released_on.append(threading.current_thread().name)
    camera = LatestFrameCamera(cap)
    time.sleep(0.05)
    camera.close()
    assert released_on == ["camera"]
