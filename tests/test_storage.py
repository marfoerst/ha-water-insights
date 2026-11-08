"""Tests for storage codecs."""
import json

from custom_components.water_insights.models import WaterEvent
from custom_components.water_insights.storage import (
    State,
    effective_label,
    event_status,
    export_events,
    import_events,
    state_from_dict,
    state_to_dict,
)

LEGACY = "\n".join(
    json.dumps(x)
    for x in [
        {"type": "p", "event_id": "20261107T201500", "t": 0.0, "f": 6.1},
        {"type": "p", "event_id": "20261107T201500", "t": 2.0, "f": 8.0},
        {"type": "event", "event_id": "20261107T201500", "start": "2026-11-07T20:15:00+01:00",
         "dauer_s": 70, "liter": 7.4, "max_flow": 8.0, "mittel_flow": 6.3, "stunde": 20,
         "wochentag": 6, "waschmaschine": False, "label": None},
        {"type": "label", "event_id": "20261107T201500", "label": "Wasserhahn", "ts": "x"},
        {"type": "label", "event_id": "20261107T201500", "label": "Toilette", "ts": "y"},
        {"type": "p", "event_id": "20261107T210000", "t": 0.0, "f": 12.0},
        {"type": "event", "event_id": "20261107T210000", "start": "2026-11-07T21:00:00+01:00",
         "dauer_s": 400, "liter": 70.0, "max_flow": 12.5, "waschmaschine": False, "label": None},
    ]
)


def _ev(i="a", label=None, src=None):
    return WaterEvent(i, 1.0, 2.0, 3.0, 1.0, 5.0, [(0.0, 5.0)], {}, label, src)


def test_roundtrip():
    s = State(events={"a": _ev("a", "Dusche", "user")}, integral_l=12.5, model={"x": 1})
    s2 = state_from_dict(json.loads(json.dumps(state_to_dict(s))))
    assert s2.events["a"].label == "Dusche" and s2.integral_l == 12.5 and s2.model == {"x": 1}


def test_import_legacy_jsonl():
    s = State()
    assert import_events(s, LEGACY) == 2
    e = s.events["20261107T201500"]
    assert e.label == "Toilette" and e.label_source == "user"
    assert e.samples == [(0.0, 6.1), (2.0, 8.0)]
    assert e.helpers == {"legacy:waschmaschine": 0.0}
    assert event_status(s.events["20261107T210000"]) == "open"


def test_import_idempotent():
    s = State()
    import_events(s, LEGACY)
    assert import_events(s, LEGACY) == 0
    assert len(s.events) == 2


def test_export_import_v1():
    s = State()
    import_events(s, LEGACY)
    s2 = State()
    assert import_events(s2, json.dumps(export_events(s))) == 2
    assert import_events(s2, export_events(s)) == 0


def test_removed_category_falls_back():
    assert effective_label(_ev(label="Garten", src="user"), ["Dusche"]) == "unclassified"
    assert effective_label(_ev(label="mixed", src="user"), ["Dusche"]) == "mixed"
    assert effective_label(_ev(), ["Dusche"]) == "unclassified"


def test_import_marks_model_stale():
    s = State(model={"type": "softmax"})
    import_events(s, LEGACY)
    assert s.model_stale
