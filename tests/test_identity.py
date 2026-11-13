import numpy as np
import pytest

from webcam_fun.identity import (
    CompanyWatch,
    MeTracker,
    OwnerTracker,
    clean_name,
    hand_owner,
    has_company,
    pick_me,
)
from webcam_fun.recognition import Enroller

# ---------- pick_me ----------

def test_pick_me_takes_best_score_above_threshold():
    assert pick_me([0.1, 0.8, 0.5], threshold=0.4) == 1


def test_pick_me_none_when_nobody_matches():
    assert pick_me([0.1, 0.3], threshold=0.4) is None
    assert pick_me([], threshold=0.4) is None


# ---------- OwnerTracker ----------

MY_FACE, TURNED = (250, 120, 120, 150), (265, 135, 110, 140)
SOMEONE_ELSE = (520, 130, 100, 130)


def test_owner_is_present_when_recognised():
    tracker = OwnerTracker()
    assert tracker.update(MY_FACE, [MY_FACE], False, 0)


def test_nobody_is_present_before_you_were_ever_recognised():
    tracker = OwnerTracker()
    assert not tracker.update(None, [SOMEONE_ELSE], True, 0)
    assert tracker.zone(640, 480) is None


def test_owner_stays_present_with_head_turned():
    tracker = OwnerTracker()
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    # The face in your spot no longer matches, and no body check is needed.
    assert all(tracker.update(None, [TURNED], False, t) for t in range(1, 60))


def test_owner_stays_present_with_head_down_while_the_body_is_there():
    tracker = OwnerTracker()
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    assert all(tracker.update(None, [], True, t) for t in range(1, 600))


def test_owner_is_gone_when_face_and_body_are_gone():
    tracker = OwnerTracker(gap_s=1.5)
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    assert not tracker.update(None, [], False, 0.1)


def test_a_face_somewhere_else_is_not_you():
    tracker = OwnerTracker()
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    assert not tracker.update(None, [SOMEONE_ELSE], False, 1)


def test_someone_who_sits_down_after_you_left_is_not_you():
    tracker = OwnerTracker(gap_s=1.5)
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    tracker.update(None, [], False, 1)
    tracker.update(None, [], False, 3)          # empty for longer than the gap
    assert not tracker.update(None, [TURNED], True, 4)   # same spot, but the chain is broken
    assert tracker.update(MY_FACE, [MY_FACE], False, 5)  # only your face brings you back


def test_a_short_blip_does_not_break_the_chain():
    tracker = OwnerTracker(gap_s=1.5)
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    assert not tracker.update(None, [], False, 1)   # one bad frame
    assert tracker.update(None, [], True, 2)


def test_owner_zone_follows_you_as_you_shift_in_the_chair():
    tracker = OwnerTracker()
    tracker.update(MY_FACE, [MY_FACE], False, 0)
    shifted = (330, 130, 115, 145)
    assert tracker.update(None, [shifted], False, 1)
    left, top, right, bottom = tracker.zone(640, 480)
    assert left < 330 and right > 445 and bottom == 480 and top == 130


def test_owner_zone_stays_inside_the_frame():
    tracker = OwnerTracker()
    tracker.update((560, 20, 120, 150), [], False, 0)
    assert tracker.zone(640, 480) == (440, 20, 640, 480)


# ---------- hand_owner ----------

ME, FRIEND = (100, 100, 100, 120), (420, 110, 90, 110)


def test_hand_belongs_to_nearest_face():
    assert hand_owner((180, 330), [ME, FRIEND]) == 0
    assert hand_owner((470, 330), [ME, FRIEND]) == 1


def test_hand_distance_is_scaled_by_face_size():
    small_far_face = (300, 100, 30, 36)  # someone at the back of the room
    # Pixel-wise the hand is nearer the small face, but that is many of *their* face widths.
    assert hand_owner((260, 300), [ME, small_far_face]) == 0


def test_hand_too_far_from_every_face_has_no_owner():
    tiny = (10, 10, 20, 24)
    assert hand_owner((600, 460), [tiny]) is None
    assert hand_owner((100, 100), []) is None


# ---------- has_company ----------

def test_company_needs_a_second_face_of_similar_size():
    assert has_company([ME, FRIEND])
    assert not has_company([ME])
    assert not has_company([])


def test_passerby_in_the_background_is_not_company():
    assert not has_company([ME, (500, 80, 22, 28)])


def test_someone_on_a_chair_just_behind_you_is_company():
    assert has_company([(200, 100, 110, 144), (480, 150, 36, 46)])  # real frame, ratio 0.32


def test_person_at_a_desk_across_the_room_is_not_company():
    assert not has_company([(200, 100, 110, 150), (480, 150, 22, 28)])  # ratio 0.19


# ---------- clean_name ----------

def test_clean_name_keeps_a_normal_name():
    assert clean_name("  Alex ") == "Alex"


@pytest.mark.parametrize("bad", ["", "   ", None, 7, "x" * 21, "tab\there", "naïve"])
def test_clean_name_falls_back_for_unusable_names(bad):
    assert clean_name(bad) == "you"


# ---------- CompanyWatch ----------

def test_company_fires_once_after_hold():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    fired = [t for t in np.arange(0, 20, 0.5) if watch.update(True, float(t))]
    assert fired == [5.0]


def test_company_countdown_survives_short_dropouts():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    assert not watch.update(True, 0)
    assert not watch.update(False, 1)   # they looked away for a moment
    assert not watch.update(True, 2.5)
    assert watch.update(True, 5)


def test_company_does_not_fire_while_they_are_out_of_view():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    watch.update(True, 0)
    watch.update(True, 3.1)
    assert not watch.update(False, 5.0)  # inside the grace, but nobody is there right now
    assert not watch.update(False, 5.2)  # grace over: the visit ended
    assert watch.state == "alone"


def test_company_fires_when_they_come_back_inside_the_grace():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    watch.update(True, 0)
    watch.update(True, 3.5)
    assert not watch.update(False, 5.2)
    assert watch.update(True, 5.4)


def test_company_countdown_resets_when_they_leave():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    watch.update(True, 0)
    watch.update(False, 1)
    assert not watch.update(False, 3.5)  # gone longer than the grace
    assert watch.seconds_left(3.5) is None
    assert not watch.update(True, 6)     # new countdown from 6
    assert watch.update(True, 11)


def test_company_rearms_after_they_leave():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    watch.update(True, 0)
    assert watch.update(True, 5)
    assert not watch.update(True, 30)    # still here: no second firing
    watch.update(False, 31)
    watch.update(False, 34)
    watch.update(True, 40)
    assert watch.update(True, 45)


def test_company_seconds_left():
    watch = CompanyWatch(hold_s=5, grace_s=2)
    assert watch.seconds_left(0) is None
    watch.update(True, 10)
    assert watch.seconds_left(12) == pytest.approx(3)
    watch.update(True, 15)
    assert watch.seconds_left(16) is None  # already fired


# ---------- MeTracker ----------

def test_me_tracker_remembers_box_for_a_short_while():
    tracker = MeTracker(grace_s=2)
    assert tracker.update(ME, 0) == ME
    assert tracker.update(None, 1.5) == ME   # hand in front of the face
    assert tracker.update(None, 2.5) is None
    assert tracker.update(None, 3) is None


def test_me_tracker_starts_empty():
    assert MeTracker(grace_s=2).update(None, 0) is None


# ---------- Enroller ----------

def _unit(v):
    v = np.asarray(v, dtype=np.float32)
    return v / np.linalg.norm(v)


def test_face_id_restarts_learning_when_the_face_is_not_alone(monkeypatch, tmp_path):
    from webcam_fun import recognition
    from webcam_fun.vision import Face

    monkeypatch.setattr(recognition, "PROFILE_FILE", tmp_path / "face_profile.npy")
    monkeypatch.setattr(recognition, "ENROLL_SAMPLES", 3)
    learned = []
    face_id = recognition.FaceId(learned.append)
    monkeypatch.setattr(face_id, "embed", lambda frame, face: face.row)
    owner = Face((0, 0, 100, 100), _unit([1, 0, 0]))
    visitor = Face((300, 0, 90, 90), _unit([0, 1, 0]))

    assert face_id.identify(None, [owner], 0.0) == 0
    assert face_id.identify(None, [owner], 1.0) == 0
    face_id.identify(None, [owner, visitor], 2.0)   # someone walked in: start over
    assert face_id.progress == 0
    passerby = Face((500, 0, 16, 20), _unit([0, 0, 1]))
    face_id.identify(None, [owner], 3.0)
    face_id.identify(None, [], 3.5)                 # one missed frame: keep going
    face_id.identify(None, [owner, passerby], 4.0)  # background face: keep going
    assert face_id.progress == pytest.approx(2 / 3)
    face_id.identify(None, [], 5.0)
    face_id.identify(None, [owner], 7.0)            # away for 3 s: start over
    assert face_id.progress == pytest.approx(1 / 3)
    for t in (8.0, 9.0):
        face_id.identify(None, [owner], t)
    assert learned == [None] and not face_id.learning
    assert face_id.identify(None, [visitor, owner], 8.0) == 1
    assert not face_id.is_you(0) and face_id.is_you(1)
    assert face_id.identify(None, [visitor], 9.0) is None
    assert not face_id.is_you(0)


# ---------- gallery ----------

def _gallery_of(*rows):
    return np.array([_unit(r) for r in rows])


def test_gallery_score_is_the_closest_sample():
    from webcam_fun.recognition import gallery_score
    gallery = _gallery_of([1, 0, 0], [0, 1, 0])
    assert gallery_score(gallery, _unit([0, 1, 0.1])) == pytest.approx(0.995, abs=0.01)


def test_gallery_grows_with_a_new_view_of_you():
    from webcam_fun.recognition import grown
    gallery = _gallery_of([1, 0, 0])
    bigger = grown(gallery, _unit([0.7, 0.7, 0]))  # similarity 0.71: you, from a new angle
    assert bigger is not None and len(bigger) == 2 and len(gallery) == 1


def test_gallery_skips_strangers_and_near_duplicates():
    from webcam_fun.recognition import grown
    gallery = _gallery_of([1, 0, 0])
    assert grown(gallery, _unit([0.2, 1, 0])) is None    # not you
    assert grown(gallery, _unit([1, 0.1, 0])) is None    # nothing new


def test_gallery_does_not_drift_away_from_the_first_sample():
    from webcam_fun.recognition import grown
    # Close to the second sample but nothing like the first: a lookalike of a lookalike.
    gallery = _gallery_of([1, 0, 0], [0.6, 0.8, 0])
    assert grown(gallery, _unit([0.1, 0.9, 0.42])) is None


def test_gallery_stops_at_its_size_limit(monkeypatch):
    from webcam_fun import recognition
    monkeypatch.setattr(recognition, "GALLERY_MAX", 1)
    assert recognition.grown(_gallery_of([1, 0, 0]), _unit([0.7, 0.7, 0])) is None


def test_old_single_sample_profile_still_loads(monkeypatch, tmp_path):
    from webcam_fun import recognition
    monkeypatch.setattr(recognition, "PROFILE_FILE", tmp_path / "face_profile.npy")
    np.save(recognition.PROFILE_FILE, np.ones(128, dtype=np.float32))
    profile = recognition.load_profile()
    assert profile.shape == (1, 128)
    assert np.linalg.norm(profile[0]) == pytest.approx(1.0)
    np.save(recognition.PROFILE_FILE, np.ones((3, 5)))
    assert recognition.load_profile() is None


def test_enroller_returns_unit_mean_after_enough_spaced_samples():
    enroller = Enroller(samples=3, min_gap_s=0.2)
    assert enroller.add(_unit([1, 0, 0]), 0.0) is None
    assert enroller.add(_unit([1, 0, 0]), 0.05) is None  # too soon, not counted
    assert enroller.progress == pytest.approx(1 / 3)
    assert enroller.add(_unit([0, 1, 0]), 0.3) is None
    profile = enroller.add(_unit([1, 0, 0]), 0.6)
    assert profile is not None
    assert np.linalg.norm(profile) == pytest.approx(1.0)
    assert profile[0] > profile[1] > profile[2]


def test_enroller_starts_over_after_a_long_gap():
    enroller = Enroller(samples=3, min_gap_s=0.2, max_gap_s=2.0)
    enroller.add(_unit([0, 1, 0]), 0.0)
    enroller.add(_unit([0, 1, 0]), 1.0)
    assert enroller.add(_unit([1, 0, 0]), 4.0) is None  # someone else may be sitting here now
    assert enroller.progress == pytest.approx(1 / 3)
    enroller.add(_unit([1, 0, 0]), 5.0)
    profile = enroller.add(_unit([1, 0, 0]), 6.0)
    assert profile[0] == pytest.approx(1.0)
