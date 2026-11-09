"""Appliance grouping and helper back-filling (pure core)."""
import math

from custom_components.water_insights.grouping import (
    appliance_groups,
    assign_helpers,
    helper_max_in_window,
)
from custom_components.water_insights.models import WaterEvent

WM = "sensor.washer_power"
RULE = {"entity_id": WM, "category": "Waschmaschine", "threshold": 10.0}
CATS = ["Toilette", "Waschmaschine"]


def ev(i, start, power=None, label=None, source=None, prediction=None, confidence=None, dur=60):
    helpers = {} if power is None else {WM: power}
    return WaterEvent(
        id=f"e{i}", start=start, end=start + dur, volume_l=8.0, duration_s=dur, max_flow=7.3,
        helpers=helpers, label=label, label_source=source, prediction=prediction, confidence=confidence,
    )


def test_helper_max_uses_state_at_start_and_changes_inside():
    series = [(0, 2.0), (100, 50.0), (130, 1800.0), (400, 3.0)]
    assert helper_max_in_window(series, 110, 170) == 1800.0
    assert helper_max_in_window(series, 110, 120) == 50.0  # value in force at start
    assert helper_max_in_window(series, 500, 560) == 3.0
    assert helper_max_in_window(series, -50, -10) is None  # nothing known yet


def test_helper_max_ignores_nan():
    assert helper_max_in_window([(0, math.nan), (10, 5.0)], 0, 20) == 5.0
    assert helper_max_in_window([(0, math.nan)], 0, 20) is None


def test_assign_helpers_fills_only_missing_keys():
    e1, e2 = ev(1, 100), ev(2, 1000, power=77.0)
    n = assign_helpers([e1, e2], {WM: [(0, 0.0), (90, 40.0)]})
    assert n == 1
    assert e1.helpers == {WM: 40.0}
    assert e2.helpers == {WM: 77.0}  # live value kept


def test_groups_split_on_gap_and_threshold():
    events = [
        ev(1, 0, power=60), ev(2, 600, power=40), ev(3, 1200, power=3),  # 3: machine off
        ev(4, 1500, power=80),
        ev(5, 10_000, power=50), ev(6, 10_500, power=90),  # new run after a long gap
        ev(7, 20_000, power=90),  # single draw: no group
    ]
    groups = appliance_groups(events, [RULE], CATS, gap_s=1800)
    assert [g["event_ids"] for g in groups] == [["e5", "e6"], ["e1", "e2", "e4"]]  # newest first
    g = groups[1]
    assert g["category"] == "Waschmaschine" and g["entity_id"] == WM
    assert g["start"] == 0 and g["end"] == 1560


def test_groups_skip_user_labels_and_preselect_by_prediction():
    events = [
        ev(1, 0, power=60, prediction="Waschmaschine", confidence=0.9),
        ev(2, 300, power=60, label="Toilette", source="user"),  # already decided
        ev(3, 600, power=60, prediction="Toilette", confidence=0.95),  # model says toilet
        ev(4, 900, power=60, prediction="Toilette", confidence=0.0),  # unfamiliar -> keep
        ev(5, 1200, power=60, label="Waschmaschine", source="auto", prediction="Waschmaschine", confidence=0.9),
    ]
    (g,) = appliance_groups(events, [RULE], CATS)
    assert g["event_ids"] == ["e1", "e3", "e4", "e5"]
    assert g["preselected"] == ["e1", "e4", "e5"]


def test_groups_ignore_rules_for_unknown_categories_and_bad_rules():
    events = [ev(1, 0, power=60), ev(2, 300, power=60)]
    assert appliance_groups(events, [{**RULE, "category": "Pool"}], CATS) == []
    assert appliance_groups(events, [{"entity_id": WM}], CATS) == []
    assert appliance_groups(events, [], CATS) == []


def test_runs_from_series_merges_short_dips():
    from custom_components.water_insights.grouping import runs_from_series

    series = [(0, 2.0), (100, 60.0), (1000, 4.0), (1300, 1800.0), (5000, 3.0), (9000, 50.0), (9500, 1.0)]
    # dip 1000-1300 (5 min) is merged; 5000-9000 is a real pause
    assert runs_from_series(series, 10.0, merge_gap_s=900) == [(100, 5000), (9000, 9500)]
    assert runs_from_series([(0, 60.0)], 10.0, until=700) == [(0, 700)]  # still running
    assert runs_from_series([], 10.0) == []


def test_groups_use_appliance_runs_when_history_known():
    # draws 50 min apart (long heating phase) still belong to one run
    events = [ev(1, 1000, power=60), ev(2, 4000, power=1800), ev(3, 7000, power=40), ev(4, 20_000, power=60),
              ev(5, 20_400, power=60)]
    runs = {WM: [(900, 8000), (19_000, 21_000)]}
    groups = appliance_groups(events, [RULE], CATS, runs_by_entity=runs)
    assert [(g["start"], g["end"], g["event_ids"]) for g in groups] == [
        (19_000, 21_000, ["e4", "e5"]),
        (900, 8000, ["e1", "e2", "e3"]),
    ]
