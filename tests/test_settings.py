import json

import pytest

from webcam_fun import settings as s
from webcam_fun.gesture_trigger import HoldTrigger
from webcam_fun.gestures import GestureApp, gesture_action

# ---------- loading and saving ----------


def test_defaults_turn_every_switch_on(tmp_path):
    loaded = s.load(tmp_path / "missing.json")
    assert all(loaded.flags[key] for key in s.ALL_SWITCHES)
    assert loaded.lock_after == s.DEFAULT_LOCK_AFTER
    assert loaded.sensitivity == s.DEFAULT_SENSITIVITY


def test_old_settings_file_still_loads(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"show_video": False, "gestures": True, "privacy": False,
                                "name": "Alex"}))
    loaded = s.load(path)
    assert loaded.flags["show_video"] is False
    assert loaded.flags["privacy"] is False
    assert loaded.flags["tabs"] is True  # new switch, default on
    assert loaded.name == "Alex"


def test_bad_values_fall_back_to_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"lock_after": 7, "sensitivity": "turbo", "name": "x" * 50}))
    loaded = s.load(path)
    assert loaded.lock_after == s.DEFAULT_LOCK_AFTER
    assert loaded.sensitivity == s.DEFAULT_SENSITIVITY
    assert loaded.name == "you"


def test_corrupt_file_gives_defaults(tmp_path):
    path = tmp_path / "settings.json"
    path.write_text("{not json")
    assert s.load(path) == s.Settings()


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "settings.json"
    original = s.toggled(s.Settings(name="Alex"), "selfie").with_lock_after(10)
    s.save(original, path)
    assert s.load(path) == original


# ---------- changing settings ----------


def test_toggled_returns_a_new_object():
    before = s.Settings()
    after = s.toggled(before, "posture")
    assert before.flags["posture"] is True
    assert after.flags["posture"] is False


def test_toggling_an_unknown_switch_fails():
    with pytest.raises(KeyError):
        s.toggled(s.Settings(), "teleport")


def test_enable_all_and_disable_all():
    off = s.with_all(s.Settings(), False)
    assert not any(off.flags[key] for key in s.ALL_SWITCHES)
    assert off.flags["show_video"] is True  # the window isn't a feature; keep it
    on = s.with_all(off, True)
    assert all(on.flags[key] for key in s.ALL_SWITCHES)
    assert s.all_on(on) and not s.all_on(off)


def test_gesture_group_toggle_covers_only_gestures():
    off = s.with_group(s.Settings(), s.GESTURE_SWITCHES, False)
    assert not any(off.flags[key] for key in s.GESTURE_SWITCHES)
    assert off.flags["auto_lock"] is True


def test_lock_after_must_be_a_known_choice():
    with pytest.raises(ValueError):
        s.Settings().with_lock_after(3)


# ---------- turning settings into desk.py flags ----------


def test_everything_on_needs_no_off_flags():
    args = s.to_args(s.Settings(name="Alex"))
    assert not [a for a in args if a.startswith("--no-")]
    assert "--gestures-off" not in args
    assert args[args.index("--name") + 1] == "Alex"
    assert args[args.index("--lock-after") + 1] == str(s.DEFAULT_LOCK_AFTER)


def test_off_switches_become_flags():
    settings = s.toggled(s.toggled(s.toggled(s.Settings(), "sounds"), "volume"), "tabs")
    args = s.to_args(settings)
    assert "--mute" in args
    assert set(args[args.index("--gestures-off") + 1].split(",")) == {"volume", "tabs"}


def test_hold_time_follows_sensitivity():
    args = s.to_args(s.Settings().with_sensitivity("careful"))
    assert float(args[args.index("--gesture-hold") + 1]) == s.SENSITIVITY["careful"]


# ---------- gestures respect the switches ----------


def test_gesture_action_names():
    assert gesture_action("Thumb_Up") == "volume"
    assert gesture_action("Closed_Fist") == "mute"
    assert gesture_action("Point_Left") == "tabs"
    assert gesture_action("Victory") == "selfie"
    assert gesture_action("Open_Palm") is None  # open palm is scrolling, not an action


class _Result:
    def __init__(self, gesture):
        self.gestures = [[type("C", (), {"category_name": gesture})()]] if gesture else [[]]
        self.hand_landmarks = [[type("L", (), {"x": 0.5, "y": 0.5})()] * 21] if gesture else []


def _app(**kwargs):
    import argparse
    # scroll off: these tests must never post real scroll events to the screen
    defaults = {"no_keys": False, "no_cursor": True, "gestures_off": "scroll",
                "gesture_hold": 0.4}
    return GestureApp(argparse.Namespace(**{**defaults, **kwargs}))


def test_disabled_gesture_never_fires(monkeypatch):
    app = _app(gestures_off="mute,scroll")
    fired = []
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    for t in range(30):
        app.step(_Result("Closed_Fist"), None, t * 0.1)
    assert fired == []


def test_enabled_gesture_fires(monkeypatch):
    app = _app()
    fired = []
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    for t in range(30):
        app.step(_Result("ILoveYou"), None, t * 0.1)
    assert fired == ["ILoveYou"]


def test_mute_needs_a_longer_hold_than_other_gestures(monkeypatch):
    app = _app()
    fired = []
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append((g, t)))
    for t in [i * 0.05 for i in range(40)]:
        app.step(_Result("Closed_Fist"), None, t)
    assert fired and fired[0][1] >= 0.75


def test_no_gestures_fire_right_after_the_pointer_is_released(monkeypatch):
    app = _app()
    fired = []
    monkeypatch.setattr(app, "act", lambda g, *_: fired.append(g))
    app.pointer_released_at = 10.0
    for t in [10 + i * 0.05 for i in range(10)]:  # 0.5 s of peace sign right after release
        app.step(_Result("ILoveYou"), None, t)
    assert fired == []


def test_hold_trigger_per_gesture_hold_time():
    trigger = HoldTrigger(hold_s=0.4, hold_overrides={"Closed_Fist": 0.8})
    assert trigger.update("Closed_Fist", 0.0) is None
    assert trigger.update("Closed_Fist", 0.5) is None
    assert trigger.update("Closed_Fist", 0.8) == "Closed_Fist"
