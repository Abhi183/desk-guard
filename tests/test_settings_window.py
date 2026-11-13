import pytest

from webcam_fun import settings as s
from webcam_fun.settings_window import CATALOG, SettingsApi


@pytest.fixture
def api(tmp_path):
    return SettingsApi(root=tmp_path, opener=lambda *_: None)


def test_state_has_every_switch_and_choices(api):
    state = api.get_state()
    assert set(state["flags"]) == {"show_video", *s.ALL_SWITCHES}
    assert state["lock_choices"] == list(s.LOCK_CHOICES)
    assert set(state["sensitivity_levels"]) == set(s.SENSITIVITY)
    assert state["status"] == "stopped"  # menu bar hasn't written a status yet


def test_catalog_covers_every_switch_once():
    keys = [item["key"] for section in CATALOG for item in section.get("items", [])]
    assert sorted(keys) == sorted({"show_video", *s.ALL_SWITCHES})


def test_set_flag_saves_to_disk(api, tmp_path):
    reply = api.set_flag("posture", False)
    assert reply["ok"] and reply["state"]["flags"]["posture"] is False
    assert s.load(tmp_path / "menubar_settings.json").flags["posture"] is False


def test_unknown_flag_is_rejected(api):
    reply = api.set_flag("teleport", True)
    assert not reply["ok"] and "teleport" in reply["error"]


def test_non_bool_flag_value_is_rejected(api):
    assert not api.set_flag("posture", "yes")["ok"]


def test_enable_all_and_disable_all(api):
    assert not any(api.set_all(False)["state"]["flags"][k] for k in s.ALL_SWITCHES)
    assert all(api.set_all(True)["state"]["flags"][k] for k in s.ALL_SWITCHES)


def test_group_switch_only_touches_that_group(api):
    flags = api.set_group("gestures", False)["state"]["flags"]
    assert not any(flags[k] for k in s.GESTURE_SWITCHES)
    assert flags["auto_lock"] is True
    assert not api.set_group("everything", False)["ok"]


def test_lock_after_and_sensitivity(api):
    assert api.set_lock_after(10)["state"]["lock_after"] == 10
    assert not api.set_lock_after(3)["ok"]
    assert api.set_sensitivity("careful")["state"]["sensitivity"] == "careful"
    assert not api.set_sensitivity("turbo")["ok"]


def test_name_is_validated(api):
    assert api.set_name("  Alex ")["state"]["name"] == "Alex"
    assert not api.set_name("x" * 50)["ok"]
    assert api.set_name("")["state"]["name"] == "you"  # empty resets to the default


def test_requests_are_files_the_menu_bar_picks_up(api, tmp_path):
    assert api.toggle_watching()["ok"]
    assert (tmp_path / ".toggle_request").exists()
    assert api.relearn_face()["ok"]
    assert (tmp_path / ".relearn_request").exists()


def test_status_comes_from_the_menu_bar(api, tmp_path):
    (tmp_path / ".status").write_text("watching\n")
    assert api.get_state()["status"] == "watching"
    (tmp_path / ".status").write_text("<script>")
    assert api.get_state()["status"] == "stopped"  # anything unexpected reads as stopped
