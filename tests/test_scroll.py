import pytest

from webcam_fun.scroll import SCROLL_GESTURE, ScrollMode, wheel_delta


def frames(mode, ys, start=0.0, dt=1 / 30, gesture=SCROLL_GESTURE):
    return [mode.update(gesture, y, start + i * dt) for i, y in enumerate(ys)]


def test_needs_a_short_hold_before_scrolling():
    mode = ScrollMode()
    steps = frames(mode, [0.5] * 8)  # ~0.25 s
    assert not any(s.active for s in steps)
    steps = frames(mode, [0.5] * 6, start=8 / 30)
    assert any(s.entered for s in steps)


def test_hand_down_gives_positive_pixels_and_up_negative():
    mode = ScrollMode(enter_s=0, smoothing=1)
    mode.update(SCROLL_GESTURE, 0.5, 0)
    assert mode.update(SCROLL_GESTURE, 0.6, 0.03).pixels == pytest.approx(240)
    assert mode.update(SCROLL_GESTURE, 0.5, 0.06).pixels == pytest.approx(-240)


def test_still_hand_does_not_scroll():
    mode = ScrollMode(enter_s=0)
    steps = frames(mode, [0.5, 0.5005, 0.4998, 0.5003, 0.5])
    assert all(s.pixels == 0 for s in steps)


def test_brief_dropout_keeps_the_page_but_a_long_one_lets_go():
    mode = ScrollMode(enter_s=0)
    mode.update(SCROLL_GESTURE, 0.5, 0)
    assert mode.update(None, None, 0.2).active
    step = mode.update(None, None, 0.6)
    assert not step.active and step.left


def test_other_gestures_never_start_scrolling():
    mode = ScrollMode(enter_s=0)
    assert not any(s.active for s in frames(mode, [0.5] * 10, gesture="Victory"))


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


def test_open_palm_moving_down_scrolls_and_fires_nothing_else(monkeypatch):
    scrolled, fired = [], []
    app = _app(monkeypatch, scrolled)
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    for i in range(30):  # hold still for the grab, then move down
        app.step(_Hand(SCROLL_GESTURE, 0.4 + max(0, i - 12) * 0.01), None, i / 30)
    assert scrolled and all(px > 0 for px in scrolled)
    assert fired == []


def test_scroll_switched_off_does_nothing(monkeypatch):
    scrolled = []
    app = _app(monkeypatch, scrolled, gestures_off="scroll")
    for i in range(30):
        app.step(_Hand(SCROLL_GESTURE, 0.4 + i * 0.01), None, i / 30)
    assert scrolled == [] and not app.scroll_mode.active


# ---------- switching between pointer, scroll and other gestures ----------


class _RealHand:
    """21 landmarks shaped like a hand: palm 0.2 long, thumb well away from the index."""

    def __init__(self, gesture, y=0.6):
        pts = [(0.5, y + 0.2)] * 21
        pts[9] = (0.5, y)            # middle knuckle
        pts[5] = (0.45, y)           # index knuckle
        pts[4] = (0.3, y)            # thumb tip
        pts[8] = (0.45, y - 0.2)     # index tip
        lm = [type("L", (), {"x": x, "y": yy})() for x, yy in pts]
        self.gestures = [[type("C", (), {"category_name": gesture})()]]
        self.hand_landmarks = [lm]


def _switch_app(monkeypatch):
    scrolled, moved, fired = [], [], []
    monkeypatch.setattr(gestures.macos, "scroll", lambda px: scrolled.append(px) or True)
    monkeypatch.setattr(gestures.macos, "natural_scrolling", lambda: True)
    monkeypatch.setattr(gestures.macos, "mouse_position", lambda: (100.0, 100.0))
    monkeypatch.setattr(gestures.macos, "desktop_bounds", lambda: (0.0, 0.0, 1000.0, 800.0))
    monkeypatch.setattr(gestures.macos, "move_mouse", lambda pos: moved.append(pos) or True)
    monkeypatch.setattr(gestures.macos, "click_mouse", lambda pos: True)
    app = gestures.GestureApp(argparse.Namespace(no_keys=True, no_cursor=False,
                                                 gestures_off="", gesture_hold=0.4))
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    return app, scrolled, moved, fired


def _run(app, gesture, start, seconds, y=0.6, dy=0.0):
    frames = int(seconds * 30)
    for i in range(frames):
        app.step(_RealHand(gesture, y + i * dy), None, start + i / 30)
    return start + frames / 30


def test_pointer_then_open_palm_switches_straight_to_scrolling(monkeypatch):
    app, scrolled, moved, _ = _switch_app(monkeypatch)
    t = _run(app, "Pointing_Up", 0, 0.6)
    assert app.cursor_mode.active
    t = _run(app, "Open_Palm", t, 0.3)              # open the hand
    assert not app.cursor_mode.active and app.scroll_mode.active
    moves_before = len(moved)
    _run(app, "Open_Palm", t, 0.5, dy=0.01)          # move it down
    assert scrolled and len(moved) == moves_before   # the page scrolls, the pointer doesn't


def test_pointer_then_two_fingers_lets_go_and_takes_the_selfie(monkeypatch):
    app, _, moved, fired = _switch_app(monkeypatch)
    t = _run(app, "Pointing_Up", 0, 0.6)
    moves_before = len(moved)
    t = _run(app, "Victory", t, 0.6, dy=0.01)
    assert not app.cursor_mode.active
    assert len(moved) == moves_before                # two fingers never drove the pointer
    _run(app, "Victory", t, 0.6)
    assert fired == ["Victory"]


def test_scrolling_then_point_up_takes_the_pointer_back(monkeypatch):
    app, _, _, _ = _switch_app(monkeypatch)
    t = _run(app, "Open_Palm", 0, 0.6)
    assert app.scroll_mode.active
    _run(app, "Pointing_Up", t, 0.6)
    assert app.cursor_mode.active and not app.scroll_mode.active
