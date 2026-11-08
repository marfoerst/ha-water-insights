"""Training with honest, chronological evaluation."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import tzinfo
import math
import time
from typing import Any

import numpy as np

from .classifier import CANDIDATES, NoveltyGuard, guarded_confidence
from .const import (
    DEFAULT_CONF_THRESHOLD,
    DEFAULT_MIN_PER_CLASS,
    DEFAULT_MIN_PRECISION,
    LABEL_MIXED,
    SOURCE_USER,
)
from .features import matrix
from .models import WaterEvent


@dataclass
class ClassMetrics:
    """Per-class evaluation result."""

    n_total: int = 0
    n_train: int = 0
    n_test: int = 0
    precision: float | None = None
    recall: float | None = None
    auto_precision: float | None = None
    auto_coverage: float | None = None
    enabled: bool = False


@dataclass
class TrainResult:
    """Outcome of a training run."""

    ok: bool
    reason: str | None = None
    model: dict[str, Any] | None = None
    chosen: str | None = None
    per_class: dict[str, ClassMetrics] = field(default_factory=dict)
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)
    trained_at: float = 0.0
    conf_threshold: float = DEFAULT_CONF_THRESHOLD
    n_events: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON-safe dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TrainResult:
        """Rebuild from dict."""
        d = dict(d)
        d["per_class"] = {k: ClassMetrics(**v) for k, v in d.get("per_class", {}).items()}
        return cls(**d)

    def enabled_classes(self) -> set[str]:
        """Classes allowed for automatic classification."""
        return {c for c, m in self.per_class.items() if m.enabled}


def training_events(events: list[WaterEvent], categories: list[str]) -> list[WaterEvent]:
    """User-labeled events whose label is a current category or mixed."""
    allowed = set(categories) | {LABEL_MIXED}
    return [e for e in events if e.label_source == SOURCE_USER and e.label in allowed]


def _split(evs: list[WaterEvent], test_fraction: float):
    evs = sorted(evs, key=lambda e: e.start)
    n_test = max(1, math.ceil(len(evs) * test_fraction))
    return evs[:-n_test], evs[-n_test:]


def train_and_evaluate(
    events: list[WaterEvent],
    categories: list[str],
    helper_ids: list[str],
    tz: tzinfo,
    min_per_class: int = DEFAULT_MIN_PER_CLASS,
    test_fraction: float = 0.25,
    min_precision: float = DEFAULT_MIN_PRECISION,
    conf_threshold: float = DEFAULT_CONF_THRESHOLD,
) -> TrainResult:
    """Train candidate models, evaluate chronologically, refit the winner."""
    labeled = training_events(events, categories)
    by_class: dict[str, list[WaterEvent]] = {}
    for e in labeled:
        by_class.setdefault(e.label, []).append(e)

    per_class = {c: ClassMetrics(n_total=len(v)) for c, v in by_class.items()}
    qualifying = sorted(c for c, v in by_class.items() if len(v) >= min_per_class)
    result = TrainResult(
        ok=False,
        per_class=per_class,
        trained_at=time.time(),
        conf_threshold=conf_threshold,
        n_events=len(labeled),
    )
    if len(qualifying) < 2:
        result.reason = "not_enough_data"
        return result

    train: list[WaterEvent] = []
    test: list[WaterEvent] = []
    for c in qualifying:
        tr, te = _split(by_class[c], test_fraction)
        train += tr
        test += te
        per_class[c].n_train, per_class[c].n_test = len(tr), len(te)
    # labeled-but-rare classes appear in the test set as unpredictable truths
    for c, v in by_class.items():
        if c not in qualifying:
            test += v
            per_class[c].n_test = len(v)

    Xtr = matrix(train, helper_ids, tz)
    ytr = [e.label for e in train]
    Xte = matrix(test, helper_ids, tz)
    yte = [e.label for e in test]

    best = None
    for name, cls in CANDIDATES.items():
        model = cls().fit(Xtr, ytr)
        guard = NoveltyGuard().fit(model.scaler.transform(Xtr), ytr)
        P, idx, conf = guarded_confidence(model, guard, Xte)
        pred = [model.classes[i] for i in idx]
        f1s = []
        for c in qualifying:
            tp = sum(p == c and t == c for p, t in zip(pred, yte))
            fp = sum(p == c and t != c for p, t in zip(pred, yte))
            fn = sum(p != c and t == c for p, t in zip(pred, yte))
            f1s.append(2 * tp / (2 * tp + fp + fn) if tp else 0.0)
        score = float(np.mean(f1s))
        if best is None or score > best[0]:
            best = (score, name, model, conf, pred)

    _, name, model, conf, pred = best
    confusion: dict[str, dict[str, int]] = {}
    for t, p in zip(yte, pred):
        confusion.setdefault(t, {}).setdefault(p, 0)
        confusion[t][p] += 1

    for c in qualifying:
        m = per_class[c]
        tp = sum(p == c and t == c for p, t in zip(pred, yte))
        n_pred = sum(p == c for p in pred)
        n_true = sum(t == c for t in yte)
        m.precision = tp / n_pred if n_pred else None
        m.recall = tp / n_true if n_true else None
        auto = [(p, t) for p, t, k in zip(pred, yte, conf) if p == c and k >= conf_threshold]
        auto_ok = sum(t == c for _, t in auto)
        m.auto_precision = auto_ok / len(auto) if auto else None
        m.auto_coverage = auto_ok / n_true if n_true else None
        m.enabled = m.auto_precision is not None and m.auto_precision >= min_precision

    Xall = matrix([e for c in qualifying for e in by_class[c]], helper_ids, tz)
    yall = [e.label for c in qualifying for e in by_class[c]]
    final = CANDIDATES[name]().fit(Xall, yall)
    final_guard = NoveltyGuard().fit(final.scaler.transform(Xall), yall)
    result.ok = True
    result.chosen = name
    result.model = {
        **final.to_dict(),
        "helper_ids": list(helper_ids),
        "guard": final_guard.to_dict(),
    }
    result.confusion = confusion
    return result
