"""Appliance runs and helper values from history (pure, no Home Assistant imports)."""
from __future__ import annotations

from bisect import bisect_right
from collections.abc import Iterable
import math
from typing import Any

from .const import SOURCE_USER
from .models import WaterEvent

DEFAULT_GROUP_GAP_S = 1800.0  # fallback without history: a pause between draws longer than this splits a run
DEFAULT_RUN_MERGE_S = 900.0  # appliance below threshold for less than this still counts as the same run
RUN_TOLERANCE_S = 120.0  # draws starting just outside a run (sensor lag) still belong to it
PRESELECT_MAX_OTHER_CONF = 0.5  # a confident prediction for another category is left unticked

Series = list[tuple[float, float]]


def helper_max_in_window(series: Series, start: float, end: float) -> float | None:
    """Maximum helper value in force during [start, end].

    `series` is a time-sorted list of (timestamp, value); NaN marks unknown values.
    The value in force at `start` is the last one recorded at or before it.
    """
    times = [t for t, _ in series]
    i = bisect_right(times, start) - 1
    window = ([series[i][1]] if i >= 0 else []) + [v for t, v in series[i + 1 :] if t <= end]
    known = [v for v in window if not math.isnan(v)]
    return max(known) if known else None


def assign_helpers(events: Iterable[WaterEvent], series_by_entity: dict[str, Series]) -> int:
    """Fill helper values that an event is missing from recorded history.

    Values an event already carries (tracked live) are never overwritten.
    Returns the number of events that changed.
    """
    changed = 0
    for ev in events:
        touched = False
        for entity_id, series in series_by_entity.items():
            if entity_id in ev.helpers or not series:
                continue
            value = helper_max_in_window(series, ev.start, ev.end)
            if value is not None:
                ev.helpers[entity_id] = value
                touched = True
        changed += touched
    return changed


def runs_from_series(
    series: Series, threshold: float, merge_gap_s: float = DEFAULT_RUN_MERGE_S, until: float | None = None
) -> list[tuple[float, float]]:
    """Periods in which a helper's value was at or above `threshold`.

    Dips shorter than `merge_gap_s` are bridged. A run still active at the end of
    the series lasts until `until` (or its last sample).
    """
    runs: list[list[float]] = []
    start: float | None = None
    for t, v in series:
        on = not math.isnan(v) and v >= threshold
        if on and start is None:
            start = t
            if runs and t - runs[-1][1] < merge_gap_s:
                start = runs.pop()[0]
        elif not on and start is not None:
            runs.append([start, t])
            start = None
    if start is not None:
        last = until if until is not None else series[-1][0]
        runs.append([start, max(last, start)])
    return [(a, b) for a, b in runs]


def _valid_rules(rules: Iterable[dict[str, Any]], categories: list[str]) -> list[dict[str, Any]]:
    out = []
    for r in rules:
        if not isinstance(r, dict) or not r.get("entity_id") or r.get("category") not in categories:
            continue
        try:
            threshold = float(r.get("threshold", 10.0))
        except (TypeError, ValueError):
            continue
        out.append({"entity_id": r["entity_id"], "category": r["category"], "threshold": threshold})
    return out


def _preselect(ev: WaterEvent, category: str) -> bool:
    if ev.label == category:
        return True
    if ev.prediction is None or ev.prediction == category:
        return True
    return (ev.confidence or 0.0) < PRESELECT_MAX_OTHER_CONF


def appliance_groups(
    events: Iterable[WaterEvent],
    rules: Iterable[dict[str, Any]],
    categories: list[str],
    gap_s: float = DEFAULT_GROUP_GAP_S,
    min_size: int = 2,
    runs_by_entity: dict[str, list[tuple[float, float]]] | None = None,
) -> list[dict[str, Any]]:
    """Group undecided draws that happened while an appliance was running.

    A rule couples a helper entity to a category: a draw belongs to the run when
    the helper's value during the draw reached `threshold`. Consecutive draws
    closer than `gap_s` form one run. When the appliance's runs are known from
    history (`runs_by_entity`), a draw belongs to the run it started in instead –
    long pauses between fills (heating, main wash) then don't split a program.
    Draws the user already labeled are left out.
    Each group lists all draws and the ones to tick by default; nothing is labeled here.
    Groups are returned newest first.
    """
    candidates = sorted(
        (e for e in events if e.label_source != SOURCE_USER), key=lambda e: e.start
    )
    groups: list[dict[str, Any]] = []
    for rule in _valid_rules(rules, categories):
        entity, category, threshold = rule["entity_id"], rule["category"], rule["threshold"]
        run: list[WaterEvent] = []

        def close(run: list[WaterEvent], bounds: tuple[float, float] | None = None) -> None:
            if len(run) >= min_size:
                start, end = bounds or (run[0].start, max(e.end for e in run))
                groups.append(
                    {
                        "entity_id": entity,
                        "category": category,
                        "start": start,
                        "end": end,
                        "event_ids": [e.id for e in run],
                        "preselected": [e.id for e in run if _preselect(e, category)],
                    }
                )

        known_runs = (runs_by_entity or {}).get(entity)
        if known_runs:
            for a, b in known_runs:
                close(
                    [e for e in candidates if a - RUN_TOLERANCE_S <= e.start <= b + RUN_TOLERANCE_S],
                    (a, b),
                )
            continue
        for ev in candidates:
            value = ev.helpers.get(entity)
            if value is None or math.isnan(value) or value < threshold:
                continue
            if run and ev.start - max(e.end for e in run) > gap_s:
                close(run)
                run = []
            run.append(ev)
        close(run)
    groups.sort(key=lambda g: g["start"], reverse=True)
    return groups
