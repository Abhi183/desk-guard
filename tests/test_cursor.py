import random

import pytest

from webcam_fun.cursor import CursorDriver, CursorMode, CursorStep, pinch_ratio

POINT, PALM, FIST, TWO = "Pointing_Up", "Open_Palm", "Closed_Fist", "Victory"
OPEN, PINCHED = 0.9, 0.1  # thumb-to-index distance in palm lengths


def entered_mode() -> CursorMode:
    mode = CursorMode(enter_s=0.3, exit_s=0.7, handoff_s=0.25, release_s=0.5)
    mode.update(POINT, OPEN, 0.0)
    assert mode.update(POINT, OPEN, 0.3).active
    return mode


# ---------- CursorMode ----------

def test_cursor_mode_starts_after_pointing_up_is_held():
    mode = CursorMode(enter_s=0.3)
    assert not mode.update(POINT, OPEN, 0.0).active
    assert not mode.update(POINT, OPEN, 0.2).active
    step = mode.update(POINT, OPEN, 0.3)
    assert step.active and step.entered


def test_cursor_mode_needs_an_unbroken_point_to_start():
    mode = CursorMode(enter_s=0.3)
    mode.update(POINT, OPEN, 0.0)
    mode.update(FIST, OPEN, 0.2)
    assert not mode.update(POINT, OPEN, 0.3).active
    assert mode.update(POINT, OPEN, 0.6).active


def test_other_gestures_do_not_start_cursor_mode():
    mode = CursorMode(enter_s=0.3)
    assert not any(mode.update(PALM, OPEN, t / 10).active for t in range(20))


def test_cursor_moves_while_active_and_keeps_going_without_a_label():
    mode = entered_mode()
    assert mode.update(POINT, OPEN, 0.4).move
    assert mode.update(None, OPEN, 0.5).move  # the model often drops the label mid-motion


def test_pinch_clicks_once_and_freezes_the_cursor():
    mode = entered_mode()
    step = mode.update(None, PINCHED, 0.5)
    assert step.left_click and not step.move
    held = mode.update(None, PINCHED, 0.6)
    assert not held.left_click and not held.move
    assert mode.update(None, OPEN, 0.7).move
    assert mode.update(None, PINCHED, 0.8).left_click  # a second pinch clicks again


def test_pinch_has_hysteresis():
    mode = entered_mode()
    assert mode.update(None, PINCHED, 0.5).left_click
    assert not mode.update(None, 0.4, 0.6).left_click   # between the two thresholds
    assert not mode.update(None, PINCHED, 0.7).left_click  # still the same pinch


def test_open_palm_hands_the_hand_over_to_scrolling():
    mode = entered_mode()
    step = mode.update(PALM, OPEN, 0.5)
    assert step.active and not step.move and not step.left_click  # freeze, don't click
    step = mode.update(PALM, OPEN, 0.75)
    assert not step.active and step.left and step.to_scroll
    assert not mode.active


def test_a_pinch_that_looks_like_an_open_palm_clicks_once_and_stays_pointer():
    mode = entered_mode()
    assert mode.update(PALM, PINCHED, 0.5).left_click
    steps = [mode.update(PALM, PINCHED, 0.5 + t / 10) for t in range(1, 12)]
    assert not any(s.left_click or s.to_scroll for s in steps)
    assert all(s.active for s in steps)
    # Fingers coming apart again must not turn into a second click straight away either.
    assert not mode.update(PALM, 0.4, 1.7).left_click


def test_switching_to_two_fingers_lets_go_of_the_pointer():
    mode = entered_mode()
    step = mode.update(TWO, OPEN, 0.4)
    assert step.active and not step.move  # the pointer stops at once
    step = mode.update(TWO, OPEN, 0.9)
    assert not step.active and step.left and not step.to_scroll


def test_a_one_frame_wrong_label_does_not_drop_the_pointer():
    mode = entered_mode()
    mode.update(TWO, OPEN, 0.4)
    assert mode.update(POINT, OPEN, 0.45).move
    assert mode.update(TWO, OPEN, 0.8).active  # the 0.5 s count started over


def test_there_is_no_right_click():
    assert "right_click" not in CursorStep._fields


def test_fist_lifts_the_mouse():
    mode = entered_mode()
    step = mode.update(FIST, OPEN, 0.5)
    assert step.active and step.lifted and not step.move
    assert mode.update(POINT, OPEN, 0.6).move


def test_palm_and_pinch_do_nothing_outside_cursor_mode():
    mode = CursorMode()
    steps = [mode.update(PALM, PINCHED, t / 10) for t in range(20)]
    assert not any(s.left_click or s.active for s in steps)


def test_cursor_mode_ends_when_the_hand_is_lowered():
    mode = entered_mode()
    assert mode.update(None, None, 0.5).active      # hand lost for a moment
    assert mode.update(None, None, 1.0).active
    step = mode.update(None, None, 1.3)
    assert not step.active and step.left
    assert not mode.update(None, OPEN, 1.4).active  # needs a new point to start again


def test_other_gestures_are_ignored_inside_cursor_mode():
    mode = entered_mode()
    step = mode.update(FIST, OPEN, 0.5)
    assert step.active and not step.left_click


# ---------- CursorDriver ----------

BOUNDS = (0.0, 0.0, 3000.0, 2000.0)
FRAME = 1 / 30


def sweep(driver, start_x, distance, seconds, t0=0.0, y=0.5):
    """Move the hand `distance` to the right over `seconds`. Returns the pointer positions."""
    frames = max(int(seconds / FRAME), 1)
    return [driver.move((start_x + distance * i / frames, y), BOUNDS, t0 + i * FRAME)
            for i in range(1, frames + 1)]


def started(cursor=(1000.0, 1000.0)) -> CursorDriver:
    driver = CursorDriver()
    driver.start((0.5, 0.5), cursor, 0.0)
    return driver


def test_driver_follows_the_hand():
    xs = [x for x, _ in sweep(started(), 0.5, 0.2, 1.0)]
    assert xs == sorted(xs) and xs[-1] > 1300


def test_driver_keeps_the_pointer_steady_on_a_trembling_hand():
    driver, rng = started(), random.Random(7)
    points = [driver.move((0.5 + rng.uniform(-0.003, 0.003), 0.5 + rng.uniform(-0.003, 0.003)),
                          BOUNDS, i * FRAME) for i in range(1, 90)]
    wobble = max(abs(x - 1000) for x, _ in points)
    assert wobble < 6  # the same tremor moved the pointer about 13 points before


def test_driver_fast_sweep_travels_further_than_a_slow_one():
    slow = sweep(started(), 0.5, 0.2, 2.0)[-1][0] - 1000
    fast = sweep(started(), 0.5, 0.2, 0.25)[-1][0] - 1000
    assert fast > slow * 1.5


def test_driver_stays_inside_the_screen():
    driver = started(cursor=(2990.0, 5.0))
    x, y = sweep(driver, 0.5, 0.4, 0.3)[-1]
    assert x == pytest.approx(2999.0)
    x, y = [driver.move((0.9, 0.5 - i * 0.02), BOUNDS, 1 + i * FRAME) for i in range(1, 20)][-1]
    assert y == 0.0


def test_driver_hold_keeps_the_pointer_put_while_the_hand_moves():
    driver = started()
    driver.hold((0.8, 0.3), 0.5)  # fist: hand moved back to a comfortable spot
    x, y = driver.move((0.8, 0.3), BOUNDS, 0.5 + FRAME)
    assert (x, y) == pytest.approx((1000.0, 1000.0))
    assert sweep(driver, 0.8, 0.1, 0.5, t0=0.6, y=0.3)[-1][0] > 1100


def test_driver_continues_from_where_the_real_mouse_put_the_pointer():
    driver = started()
    driver.sync((100.0, 100.0))  # you nudged the trackpad meanwhile
    x, y = driver.move((0.5, 0.5), BOUNDS, FRAME)
    assert (x, y) == pytest.approx((100.0, 100.0))


# ---------- pinch_ratio ----------

def _hand(thumb_tip, index_tip):
    pts = [(0.5, 0.8)] * 21
    pts[9] = (0.5, 0.6)  # middle knuckle: palm length 0.2
    pts[4], pts[8] = thumb_tip, index_tip
    return pts


def test_pinch_ratio_is_in_palm_lengths():
    assert pinch_ratio(_hand((0.5, 0.5), (0.52, 0.5))) == pytest.approx(0.1)
    assert pinch_ratio(_hand((0.4, 0.6), (0.5, 0.4))) > 1.0


def test_pinch_ratio_needs_a_whole_hand():
    assert pinch_ratio([(0.5, 0.5)] * 5) is None
    assert pinch_ratio([(0.5, 0.5)] * 21) is None  # zero-size palm
