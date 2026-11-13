import pytest

from webcam_fun import settings as s
from webcam_fun.settings_api import CATALOG, SettingsApi


class FakeHost:
    def __init__(self):
        self.applied = []
        self.calls = []
        self.current = "watching"

    def status(self):
        return self.current

    def apply_settings(self, settings):
        self.applied.append(settings)

    def toggle_watching(self):
        self.calls.append("toggle")

    def relearn_face(self):
        self.calls.append("relearn")

    def recalibrate_posture(self):
        self.calls.append("recalibrate")


@pytest.fixture
def host():
    return FakeHost()


@pytest.fixture
def opened():
    return []


@pytest.fixture
def api(tmp_path, host, opened):
    return SettingsApi(tmp_path / "settings.json", tmp_path, host, opened.append)


def test_state_has_every_switch_and_choices(api):
    state = api.get_state()
    assert set(state["flags"]) == {*s.DISPLAY_SWITCHES, *s.ALL_SWITCHES}
    assert state["lock_choices"] == list(s.LOCK_CHOICES)
    assert set(state["sensitivity_levels"]) == set(s.SENSITIVITY)
    assert state["status"] == "watching"


def test_catalog_covers_every_switch_once():
    keys = [item["key"] for section in CATALOG for item in section.get("items", [])]
    assert sorted(keys) == sorted({*s.DISPLAY_SWITCHES, *s.ALL_SWITCHES})


def test_change_is_saved_and_applied_live(api, host, tmp_path):
    reply = api.set_flag("posture", False)
    assert reply["ok"] and reply["state"]["flags"]["posture"] is False
    assert s.load(tmp_path / "settings.json").flags["posture"] is False
    assert host.applied[-1].flags["posture"] is False


def test_rejected_change_is_not_applied(api, host):
    assert not api.set_flag("teleport", True)["ok"]
    assert not api.set_flag("posture", "yes")["ok"]
    assert host.applied == []


def test_enable_all_and_disable_all(api):
    assert not any(api.set_all(False)["state"]["flags"][k] for k in s.ALL_SWITCHES)
    flags = api.set_all(True)["state"]["flags"]
    assert all(flags[k] for k in s.ALL_SWITCHES)
    assert flags["show_video"] and flags["video_on_top"]  # display switches untouched


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
    assert api.set_name("")["state"]["name"] == "you"


def test_actions_go_to_the_host(api, host):
    api.toggle_watching()
    api.relearn_face()
    api.recalibrate_posture()
    assert host.calls == ["toggle", "relearn", "recalibrate"]


def test_open_folder_only_known_names(api, opened, tmp_path):
    assert api.open_folder("caught")["ok"]
    assert opened[-1] == ["open", str(tmp_path / "caught")]
    assert not api.open_folder("../../etc")["ok"]


def test_bridge_refuses_unknown_and_private_methods(api):
    assert not api.call("_change", [])["ok"]
    assert not api.call("__init__", [])["ok"]
    assert not api.call("set_flag", "posture")["ok"]  # args must be a list
    assert not api.call("set_flag", [])["ok"]          # wrong argument count
    assert api.call("set_flag", ["sounds", False])["ok"]


def test_unknown_status_reads_as_stopped(api, host):
    host.current = "<script>"
    assert api.get_state()["status"] == "stopped"
