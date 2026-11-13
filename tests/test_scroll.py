import pytest

from webcam_fun.scroll import TWO_FINGERS, ScrollMode, wheel_delta


def frames(mode, ys, start=0.0, dt=1 / 30, gesture=TWO_FINGERS):
    return [mode.update(gesture, y, start + i * dt) for i, y in enumerate(ys)]


def test_needs_a_short_hold_before_scrolling():
    mode = ScrollMode()
    steps = frames(mode, [0.5] * 4)  # ~0.1 s
    assert not any(s.active for s in steps)
    steps = frames(mode, [0.5] * 6, start=4 / 30)
    assert any(s.entered for s in steps)


def test_hand_down_gives_positive_pixels_and_up_negative():
    mode = ScrollMode(enter_s=0, smoothing=1)
    mode.update(TWO_FINGERS, 0.5, 0)
    assert mode.update(TWO_FINGERS, 0.6, 0.03).pixels == pytest.approx(240)
    assert mode.update(TWO_FINGERS, 0.5, 0.06).pixels == pytest.approx(-240)


def test_still_hand_does_not_scroll():
    mode = ScrollMode(enter_s=0)
    steps = frames(mode, [0.5, 0.5005, 0.4998, 0.5003, 0.5])
    assert all(s.pixels == 0 for s in steps)


def test_brief_dropout_keeps_the_page_but_a_long_one_lets_go():
    mode = ScrollMode(enter_s=0)
    mode.update(TWO_FINGERS, 0.5, 0)
    assert mode.update(None, None, 0.2).active
    step = mode.update(None, None, 0.6)
    assert not step.active and step.left


def test_other_gestures_never_start_scrolling():
    mode = ScrollMode(enter_s=0)
    assert not any(s.active for s in frames(mode, [0.5] * 10, gesture="Open_Palm"))


def test_wheel_direction_follows_natural_scrolling():
    assert wheel_delta(100.4, natural=True) == 100
    assert wheel_delta(100.4, natural=False) == -100


# ---------- inside GestureApp ----------

import argparse  # noqa: E402

from webcam_fun import gestures  # noqa: E402


class _Hand:
    def __init__(self, gesture, y):
        lm = type("L", (), {"x": 0.5, "y": y})
        self.gestures = [[type("C", (), {"category_name": gesture})()]]
        self.hand_landmarks = [[lm() for _ in range(21)]]


def _app(monkeypatch, scrolled, gestures_off=""):
    monkeypatch.setattr(gestures.macos, "scroll", lambda px: scrolled.append(px) or True)
    monkeypatch.setattr(gestures.macos, "natural_scrolling", lambda: True)
    return gestures.GestureApp(argparse.Namespace(no_keys=True, no_cursor=True,
                                                  gestures_off=gestures_off, gesture_hold=0.4))


def test_two_fingers_moving_down_scrolls_and_fires_nothing_else(monkeypatch):
    scrolled, fired = [], []
    app = _app(monkeypatch, scrolled)
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    for i in range(30):  # hold still for the grab, then move down
        app.step(_Hand(TWO_FINGERS, 0.4 + max(0, i - 10) * 0.01), None, i / 30)
    assert scrolled and all(px > 0 for px in scrolled)
    assert fired == []


def test_scroll_switched_off_does_nothing(monkeypatch):
    scrolled = []
    app = _app(monkeypatch, scrolled, gestures_off="scroll")
    for i in range(30):
        app.step(_Hand(TWO_FINGERS, 0.4 + i * 0.01), None, i / 30)
    assert scrolled == [] and not app.scroll_mode.active
