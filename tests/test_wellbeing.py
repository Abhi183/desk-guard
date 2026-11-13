import json

import pytest

from webcam_fun.stats import BREAK_S, DeskTime, StatsStore
from webcam_fun.water import WaterReminder

# ---------- water ----------


def test_reminds_after_the_interval_of_desk_time():
    water = WaterReminder(every_s=600)
    assert not water.update(599)
    assert water.update(1)


def test_ignored_reminder_comes_back_sooner():
    water = WaterReminder(every_s=3600, remind_again_s=900)
    assert water.update(3600)
    assert not water.update(899)
    assert water.update(1)


def test_a_glass_starts_the_full_interval_again():
    water = WaterReminder(every_s=600)
    water.update(500)
    water.drank()
    assert not water.update(599)
    assert water.update(1)


def test_changing_the_interval_keeps_time_already_counted():
    water = WaterReminder(every_s=3600)
    water.update(1800)
    water.set_every(2700)
    assert not water.update(899)
    assert water.update(1)


# ---------- desk time ----------


def test_desk_time_counts_while_seen_and_through_short_glances_away():
    desk = DeskTime(away_grace_s=20)
    total = 0.0
    for t in range(0, 10):
        total += desk.update(True, t)[0]
    for t in range(10, 25):  # looked away for 15 s: still at the desk
        total += desk.update(False, t)[0]
    assert total == pytest.approx(24)


def test_desk_time_stops_after_the_grace():
    desk = DeskTime(away_grace_s=5)
    desk.update(True, 0)
    added = [desk.update(False, t)[0] for t in range(1, 30)]
    assert sum(added) == pytest.approx(5)


def test_coming_back_after_a_long_absence_is_a_break():
    desk = DeskTime()
    desk.update(True, 0)
    assert desk.update(True, 60)[1] is False
    assert desk.update(True, 60 + BREAK_S)[1] is True


def test_a_stalled_camera_does_not_add_a_big_chunk():
    desk = DeskTime()
    desk.update(True, 0)
    assert desk.update(True, 120)[0] == 1.0


# ---------- stats store ----------


def test_counts_go_to_today_and_survive_a_restart(tmp_path):
    path = tmp_path / "stats.json"
    store = StatsStore(path, today=lambda: "2026-11-13")
    store.add("water_glasses")
    store.add("desk_s", 90)
    store.save()
    again = StatsStore(path, today=lambda: "2026-11-13")
    today = again.snapshot()["today"]
    assert today["water_glasses"] == 1 and today["desk_s"] == 90


def test_week_has_seven_days_ending_today(tmp_path):
    store = StatsStore(tmp_path / "s.json", today=lambda: "2026-11-13")
    week = store.snapshot()["week"]
    assert [d["date"] for d in week][0] == "2026-11-07" and week[-1]["date"] == "2026-11-13"


def test_good_posture_share_needs_a_minute_of_data(tmp_path):
    store = StatsStore(tmp_path / "s.json", today=lambda: "2026-11-13")
    store.add("posture_judged_s", 30)
    assert store.snapshot()["today"]["good_posture"] is None
    store.add("posture_judged_s", 70)
    store.add("posture_good_s", 75)
    assert store.snapshot()["today"]["good_posture"] == pytest.approx(0.75)


def test_unknown_keys_and_junk_files_are_rejected(tmp_path):
    path = tmp_path / "s.json"
    path.write_text(json.dumps({"2026-11-13": {"desk_s": -5, "evil": 1, "locks": 2},
                                "x": "nope"}))
    store = StatsStore(path, today=lambda: "2026-11-13")
    today = store.snapshot()["today"]
    assert today["locks"] == 2 and today["desk_s"] == 0
    with pytest.raises(KeyError):
        store.add("evil")


def test_old_days_are_pruned(tmp_path):
    path = tmp_path / "s.json"
    days = {f"2026-10-{d:02d}": {"locks": 1} for d in range(1, 32)}
    days.update({f"2026-11-{d:02d}": {"locks": 1} for d in range(1, 14)})
    path.write_text(json.dumps(days))
    store = StatsStore(path, today=lambda: "2026-11-13")
    store.add("locks")
    store.save()
    assert len(json.loads(path.read_text())) == 30
