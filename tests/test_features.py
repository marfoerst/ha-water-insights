"""Tests for feature extraction and synthetic data."""
from datetime import UTC
import math

from custom_components.water_insights.features import BASE_FEATURES, extract
from custom_components.water_insights.models import WaterEvent
from tools.synth import HELPER, generate


def _ev(start=1_800_000_000.0, samples=None, helpers=None):
    samples = samples or [(i * 2.0, 10.0) for i in range(60)]
    return WaterEvent("x", start, start + 120, 20.0, 120.0, 10.0, samples, helpers or {})


def test_feature_vector_length():
    assert len(extract(_ev(), ["a", "b"], UTC)) == len(BASE_FEATURES) + 2


def test_hour_cyclic():
    from datetime import datetime

    late = datetime(2026, 1, 1, 23, 59, tzinfo=UTC).timestamp()
    early = datetime(2026, 1, 2, 0, 1, tzinfo=UTC).timestamp()
    a, b = extract(_ev(late), [], UTC), extract(_ev(early), [], UTC)
    i, j = BASE_FEATURES.index("hour_sin"), BASE_FEATURES.index("hour_cos")
    assert math.hypot(a[i] - b[i], a[j] - b[j]) < 0.05


def test_missing_helper_nan():
    v = extract(_ev(helpers={"a": 5.0}), ["a", "b"], UTC)
    assert v[-2] == 5.0 and math.isnan(v[-1])


def test_synth_deterministic():
    a, b = generate(10, seed=3), generate(10, seed=3)
    assert [e.to_dict() for e in a] == [e.to_dict() for e in b]
    assert {e.label for e in a} >= {"Dusche", "Toilette", "Wasserhahn", "Waschmaschine"}


def test_plateau_ratio_shower_vs_washer():
    ev = generate(30, seed=2)
    i = BASE_FEATURES.index("plateau_ratio")
    sh = [extract(e, [HELPER], UTC)[i] for e in ev if e.label == "Dusche"]
    wm = [extract(e, [HELPER], UTC)[i] for e in ev if e.label == "Toilette"]
    assert sum(sh) / len(sh) > sum(wm) / len(wm)
