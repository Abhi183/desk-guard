import numpy as np
import pytest

from webcam_fun.ascii_art import fit_size, to_levels
from webcam_fun.gesture_trigger import HoldTrigger
from webcam_fun.posture import PostureJudge
from webcam_fun.presence import AutoLock
from webcam_fun.watch import ShoulderWatch

# ---------- AutoLock ----------

def test_autolock_does_not_lock_before_a_face_was_ever_seen():
    lock = AutoLock(timeout_s=5)
    assert not any(lock.update(False, t) for t in range(30))
    assert lock.state == "waiting"


def test_autolock_locks_once_after_timeout_without_face():
    lock = AutoLock(timeout_s=5)
    lock.update(True, 0)
    fired = [t for t in np.arange(0.5, 20, 0.5) if lock.update(False, float(t))]
    assert fired == [5.5]
    assert lock.state == "locked"


def test_autolock_countdown_resets_when_face_returns():
    lock = AutoLock(timeout_s=5)
    lock.update(True, 0)
    lock.update(False, 1)
    assert lock.seconds_left(3) == pytest.approx(3)
    lock.update(True, 4)
    assert lock.seconds_left(4) is None
    assert not lock.update(False, 8)  # new countdown started at 8
    assert lock.update(False, 13.1)


def test_autolock_rearms_after_user_comes_back():
    lock = AutoLock(timeout_s=2)
    lock.update(True, 0)
    lock.update(False, 1)
    assert lock.update(False, 3.5)
    lock.update(True, 10)
    assert lock.state == "armed"
    lock.update(False, 11)
    assert lock.update(False, 13.5)


def test_autolock_short_glitches_do_not_lock():
    lock = AutoLock(timeout_s=3)
    lock.update(True, 0)
    for t in range(1, 50):
        assert not lock.update(t % 2 == 0, float(t))


# ---------- ShoulderWatch ----------

def test_watch_needs_sustained_extra_face():
    watch = ShoulderWatch(window=10, trigger_hits=6)
    for _ in range(5):
        assert not watch.update(True)
    assert watch.update(True)  # 6th hit fires
    assert watch.alarm
    assert not watch.update(True)  # only fires on the transition


def test_watch_single_glitches_never_alarm():
    watch = ShoulderWatch(window=10, trigger_hits=6)
    assert not any(watch.update(i % 3 == 0) for i in range(100))


def test_watch_clears_after_window_of_clean_frames():
    watch = ShoulderWatch(window=4, trigger_hits=3)
    for _ in range(3):
        watch.update(True)
    for _ in range(3):
        watch.update(False)
    assert watch.alarm
    watch.update(False)
    assert not watch.alarm


# ---------- PostureJudge ----------

def calibrated_judge(**kw):
    judge = PostureJudge(calib_frames=3, hold_s=2, cooldown_s=30, **kw)
    for t in range(3):
        judge.update((200, 100, 100, 100), 480, float(t))
    return judge


def test_posture_calibrates_first():
    judge = PostureJudge(calib_frames=3)
    assert judge.update((200, 100, 100, 100), 480, 0)[0] == "calibrating"


def test_posture_good_when_matching_baseline():
    judge = calibrated_judge()
    assert judge.update((205, 102, 98, 101), 480, 5) == ("good", False)


def test_posture_too_close_alerts_after_hold():
    judge = calibrated_judge()
    big = (180, 80, 130, 130)
    assert judge.update(big, 480, 10) == ("too_close", False)
    assert judge.update(big, 480, 11) == ("too_close", False)
    assert judge.update(big, 480, 12.1) == ("too_close", True)
    assert judge.update(big, 480, 13) == ("too_close", False)  # cooldown


def test_posture_slouch_when_face_drops():
    judge = calibrated_judge()
    low = (200, 160, 100, 100)  # 60px lower on a 480px frame
    assert judge.update(low, 480, 10)[0] == "slouching"


def test_posture_recalibrate():
    judge = calibrated_judge()
    judge.recalibrate()
    assert judge.update((200, 100, 100, 100), 480, 20)[0] == "calibrating"


def test_posture_no_face():
    judge = calibrated_judge()
    assert judge.update(None, 480, 10) == ("no_face", False)


# ---------- HoldTrigger ----------

def test_hold_fires_once_for_one_shot_gesture():
    trig = HoldTrigger(hold_s=0.5, repeat_s={})
    fired = [trig.update("Closed_Fist", t / 10) for t in range(30)]
    assert fired.count("Closed_Fist") == 1


def test_hold_repeats_for_repeatable_gesture():
    trig = HoldTrigger(hold_s=0.5, repeat_s={"Thumb_Up": 0.5})
    fired = [trig.update("Thumb_Up", t / 10) for t in range(21)]
    assert fired.count("Thumb_Up") == 4  # at 0.5, 1.0, 1.5, 2.0


def test_hold_resets_on_change():
    trig = HoldTrigger(hold_s=0.5, repeat_s={})
    trig.update("Victory", 0.0)
    trig.update("None", 0.3)
    assert trig.update("Victory", 0.6) is None
    assert trig.update("Victory", 1.2) == "Victory"


# ---------- ASCII ----------

def test_fit_size_keeps_aspect_and_fits():
    cols, rows = fit_size(640, 480, term_cols=200, term_rows=50)
    assert cols <= 200 and rows <= 49
    assert cols / (rows * 2) == pytest.approx(640 / 480, rel=0.05)


def test_to_levels_maps_black_and_white_to_extremes():
    gray = np.zeros((10, 20), np.uint8)
    gray[:, 10:] = 255
    levels = to_levels(gray, cols=4, rows=2, n_levels=10)
    assert levels.shape == (2, 4)
    assert levels[0, 0] == 0 and levels[0, 3] == 9


def test_hold_tolerates_brief_dropouts():
    trig = HoldTrigger(hold_s=0.5, repeat_s={}, grace_s=0.25)
    seq = ["Thumb_Up", "Thumb_Up", "None", "Thumb_Up", None, "Thumb_Up", "Thumb_Up"]
    fired = [trig.update(g, i * 0.1) for i, g in enumerate(seq)]
    assert fired.count("Thumb_Up") == 1


def test_hold_progress():
    trig = HoldTrigger(hold_s=0.5, repeat_s={})
    trig.update("Victory", 0.0)
    trig.update("Victory", 0.25)
    assert trig.progress(0.25) == pytest.approx(0.5)
    assert trig.progress(1.0) == 1.0


# ---------- pointing left / right ----------

def _pointing_hand(direction: str) -> list[tuple[float, float]]:
    """21 MediaPipe-style landmarks: index finger out sideways, other fingers curled."""
    pts = [(0.5, 0.8)] * 21
    right = {0: (0.5, 0.8), 5: (0.55, 0.62), 6: (0.62, 0.62), 7: (0.68, 0.62), 8: (0.74, 0.62),
             9: (0.5, 0.6), 10: (0.5, 0.55), 12: (0.52, 0.65), 14: (0.46, 0.56),
             16: (0.47, 0.66), 18: (0.43, 0.58), 20: (0.44, 0.67)}
    pts = [right.get(i, p) for i, p in enumerate(pts)]
    if direction == "left":
        pts = [(1.0 - x, y) for x, y in pts]
    return pts


def test_point_right_and_left():
    from webcam_fun.pointing import classify_point
    assert classify_point(_pointing_hand("right")) == "Point_Right"
    assert classify_point(_pointing_hand("left")) == "Point_Left"


def test_pointing_up_is_not_sideways():
    from webcam_fun.pointing import classify_point
    pts = _pointing_hand("right")
    pts[6], pts[7], pts[8] = (0.55, 0.5), (0.55, 0.42), (0.55, 0.34)
    assert classify_point(pts) is None


def test_open_hand_is_not_pointing():
    from webcam_fun.pointing import classify_point
    pts = _pointing_hand("right")
    pts[12], pts[16], pts[20] = (0.5, 0.3), (0.45, 0.32), (0.4, 0.36)  # other fingers out
    assert classify_point(pts) is None


def test_curled_index_is_not_pointing():
    from webcam_fun.pointing import classify_point
    pts = _pointing_hand("right")
    pts[8] = (0.56, 0.68)  # tip folded back toward the palm
    assert classify_point(pts) is None


def test_too_few_landmarks():
    from webcam_fun.pointing import classify_point
    assert classify_point([(0.5, 0.5)] * 5) is None

