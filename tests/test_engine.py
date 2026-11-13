import argparse
import threading
import time

import numpy as np

from webcam_fun.gestures import GestureApp


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


FACETIME = engine.Camera("builtin-1", "FaceTime HD Camera", True)
USB = engine.Camera("usb-2", "Desk Camera", False)


class FakeSession:
    """Stands in for AVCamera: records what was opened, gives frames on request."""

    def __init__(self, works=True):
        self.works = works
        self.uid = None
        self.calls = []
        self.closed = False

    def open(self, uid):
        self.calls.append(("open", uid))
        self.uid = uid if self.works else None
        return self.works

    def switch_to(self, uid):
        self.calls.append(("switch", uid))
        if self.works:
            self.uid = uid
        return self.works

    def read(self, last_seq, timeout):
        return np.zeros((4, 4, 3), np.uint8), last_seq + 1

    def close(self):
        self.closed = True


def _watcher(monkeypatch, statuses, works=True, cameras=(FACETIME,)):
    monkeypatch.setattr(engine, "AVCamera", lambda: FakeSession(works))
    monkeypatch.setattr(engine, "list_cameras", lambda: list(cameras))
    return engine.Watcher(cfg.Settings(), on_status=statuses.append)


def test_camera_that_gives_no_picture_reports_blocked_and_ends(monkeypatch):
    statuses = []
    w = _watcher(monkeypatch, statuses, works=False)
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


def test_feed_opens_the_camera_by_its_id_and_switches_in_place():
    sessions = []
    feed = engine._Feed(opener=lambda: sessions.append(FakeSession()) or sessions[-1])
    assert feed.open(USB) and feed.camera == USB
    assert feed.switch(FACETIME) and feed.camera == FACETIME
    assert len(sessions) == 1  # switching reused the running session
    assert sessions[0].calls == [("open", "usb-2"), ("switch", "builtin-1")]
    feed.close()
    assert sessions[0].closed and feed.reader is None


def test_failed_switch_keeps_the_old_camera():
    session = FakeSession()
    feed = engine._Feed(opener=lambda: session)
    feed.open(USB)
    session.works = False
    assert not feed.switch(FACETIME)
    assert feed.camera == USB


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
