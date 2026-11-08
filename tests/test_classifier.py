"""Tests for classifiers and evaluation."""
from datetime import UTC
import time

import numpy as np

from custom_components.water_insights.classifier import model_from_dict
from custom_components.water_insights.const import DEFAULT_CATEGORIES
from custom_components.water_insights.evaluation import TrainResult, train_and_evaluate
from custom_components.water_insights.features import matrix
from tools.synth import HELPER, generate

CATS = DEFAULT_CATEGORIES


def test_synthetic_main_classes_precise():
    res = train_and_evaluate(generate(60, seed=1), CATS, [HELPER], UTC)
    assert res.ok
    for c in ("Dusche", "Toilette", "Waschmaschine"):
        m = res.per_class[c]
        assert m.auto_precision is not None and m.auto_precision >= 0.9, (c, m)
        assert m.enabled


def test_not_enough_data():
    evs = []
    for c in ("Dusche", "Toilette", "Wasserhahn"):
        evs += [e for e in generate(30, seed=4) if e.label == c][:5]
    res = train_and_evaluate(evs, CATS, [HELPER], UTC)
    assert not res.ok and res.reason == "not_enough_data" and res.model is None
    assert res.per_class["Dusche"].n_total == 5


def test_chronological_split():
    evs = generate(40, seed=5)
    res = train_and_evaluate(evs, CATS, [HELPER], UTC, test_fraction=0.25)
    assert res.ok
    for c, m in res.per_class.items():
        if m.n_train:
            assert m.n_test >= 1
            assert m.n_train + m.n_test == m.n_total


def test_model_roundtrip():
    evs = generate(30, seed=6)
    res = train_and_evaluate(evs, CATS, [HELPER], UTC)
    m1 = model_from_dict(res.model)
    m2 = model_from_dict(TrainResult.from_dict(res.to_dict()).model)
    X = matrix(evs[:20], [HELPER], UTC)
    assert np.allclose(m1.predict_proba(X), m2.predict_proba(X))


def test_auto_labels_excluded():
    evs = generate(30, seed=7)
    for e in evs:
        if e.label == "Toilette":
            e.label_source = "auto"
    res = train_and_evaluate(evs, CATS, [HELPER], UTC)
    assert "Toilette" not in res.per_class


def test_removed_category_not_trained():
    evs = generate(30, seed=8)
    res = train_and_evaluate(evs, ["Dusche", "Toilette"], [HELPER], UTC)
    assert set(res.per_class) <= {"Dusche", "Toilette", "mixed"}


def test_training_speed():
    evs = generate(400, seed=9)  # several thousand events
    t = time.monotonic()
    res = train_and_evaluate(evs, CATS, [HELPER], UTC)
    assert res.ok
    assert time.monotonic() - t < 30


def test_needs_auto_evidence():
    evs = generate(60, seed=1)
    res = train_and_evaluate(evs, CATS, [HELPER], UTC)
    garden = res.per_class.get("Garten")
    assert garden is not None and garden.n_total < 20 and not garden.enabled
