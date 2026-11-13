import json

import pytest

from webcam_fun import settings as s
from webcam_fun.cameras import Camera, choose

BUILT_IN = Camera("builtin-1", "FaceTime HD Camera", True)
EXTERNAL = Camera("usb-2", "Desk Camera", False)
OTHER = Camera("usb-3", "Second Camera", False)
IPHONE = Camera("phone-4", "Abhi iPhone Camera", False, continuity=True)


def test_auto_prefers_an_external_camera():
    assert choose([BUILT_IN, EXTERNAL], s.AUTO_CAMERA) == EXTERNAL


def test_auto_never_prefers_the_iphone():
    assert choose([IPHONE, BUILT_IN], s.AUTO_CAMERA) == BUILT_IN
    assert choose([IPHONE, BUILT_IN, EXTERNAL], s.AUTO_CAMERA) == EXTERNAL
    assert choose([IPHONE], s.AUTO_CAMERA) == IPHONE  # the only camera left


def test_iphone_can_still_be_picked():
    assert choose([BUILT_IN, IPHONE], "phone-4") == IPHONE


def test_auto_falls_back_to_the_built_in_camera():
    assert choose([BUILT_IN], s.AUTO_CAMERA) == BUILT_IN


def test_picked_camera_wins_even_over_external():
    assert choose([BUILT_IN, EXTERNAL, OTHER], "builtin-1") == BUILT_IN
    assert choose([BUILT_IN, EXTERNAL, OTHER], "usb-3") == OTHER


def test_unplugged_pick_uses_the_automatic_choice_meanwhile():
    assert choose([BUILT_IN, EXTERNAL], "gone-9") == EXTERNAL
    assert choose([BUILT_IN], "usb-2") == BUILT_IN


def test_no_cameras():
    assert choose([], s.AUTO_CAMERA) is None


def test_camera_choice_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    s.save(s.Settings().with_camera("usb-2", "Desk Camera"), path)
    loaded = s.load(path)
    assert (loaded.camera, loaded.camera_name) == ("usb-2", "Desk Camera")
    assert loaded.with_camera(s.AUTO_CAMERA).camera_name == ""


@pytest.mark.parametrize("bad", ["", None, 7, "x" * 500])
def test_bad_camera_ids_are_refused(bad):
    with pytest.raises(ValueError):
        s.Settings().with_camera(bad)


def test_bad_camera_in_file_falls_back_to_auto(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"camera": 12, "camera_name": "x"}))
    assert s.load(path).camera == s.AUTO_CAMERA
