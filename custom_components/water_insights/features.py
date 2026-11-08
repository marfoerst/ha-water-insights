"""Feature extraction for water draws (numpy only, no Home Assistant)."""
from __future__ import annotations

from datetime import datetime, tzinfo
import math

import numpy as np

from .models import WaterEvent

BASE_FEATURES: list[str] = [
    "log_volume",
    "log_duration",
    "log_max_flow",
    "mean_flow",
    "flow_cv",
    "n_pauses",
    "plateau_ratio",
    "hour_sin",
    "hour_cos",
    "weekday_sin",
    "weekday_cos",
]

PAUSE_FLOW = 1.0


def feature_names(helper_ids: list[str]) -> list[str]:
    """Names of all features for a given helper list."""
    return BASE_FEATURES + [f"helper:{h}" for h in helper_ids]


def _flow_stats(samples: list[tuple[float, float]]) -> tuple[float, int, float]:
    flows = np.array([f for _, f in samples], dtype=float)
    active = flows[flows >= PAUSE_FLOW]
    if active.size == 0:
        return 0.0, 0, 0.0
    mean = float(active.mean())
    cv = float(active.std() / mean) if mean > 0 else 0.0
    above = flows >= PAUSE_FLOW
    # count above->below transitions that are followed by flow again
    pauses = 0
    for i in range(1, len(above)):
        if above[i - 1] and not above[i] and above[i:].any():
            pauses += 1
    med = float(np.median(active))
    plateau = float(np.mean(np.abs(active - med) <= 0.2 * med)) if med > 0 else 0.0
    return cv, pauses, plateau


def extract(event: WaterEvent, helper_ids: list[str], tz: tzinfo) -> np.ndarray:
    """Return the feature vector of an event."""
    cv, pauses, plateau = _flow_stats(event.samples)
    local = datetime.fromtimestamp(event.start, tz)
    hour = local.hour + local.minute / 60
    wd = local.weekday()
    duration = max(event.duration_s, 1.0)
    base = [
        math.log1p(event.volume_l),
        math.log1p(duration),
        math.log1p(event.max_flow),
        event.volume_l / duration * 60.0,
        cv,
        float(pauses),
        plateau,
        math.sin(2 * math.pi * hour / 24),
        math.cos(2 * math.pi * hour / 24),
        math.sin(2 * math.pi * wd / 7),
        math.cos(2 * math.pi * wd / 7),
    ]
    helpers = [float(event.helpers.get(h, math.nan)) for h in helper_ids]
    return np.array(base + helpers, dtype=float)


def matrix(events: list[WaterEvent], helper_ids: list[str], tz: tzinfo) -> np.ndarray:
    """Feature matrix for a list of events."""
    if not events:
        return np.zeros((0, len(feature_names(helper_ids))))
    return np.vstack([extract(e, helper_ids, tz) for e in events])
