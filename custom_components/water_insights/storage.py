"""Persistent state and import/export codecs (no Home Assistant imports)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from typing import Any

from .const import (
    LABEL_MIXED,
    LABEL_UNCLASSIFIED,
    SOURCE_USER,
    STATUS_AUTO,
    STATUS_CONFIRMED,
    STATUS_OPEN,
    STATUS_SKIPPED,
)
from .models import WaterEvent

EXPORT_VERSION = 1


@dataclass
class State:
    """Everything the integration persists."""

    events: dict[str, WaterEvent] = field(default_factory=dict)
    integral_l: float = 0.0
    model: dict[str, Any] | None = None
    train_result: dict[str, Any] | None = None
    model_stale: bool = False
    backfill_done: bool = False
    counting_since: float | None = None


def state_to_dict(state: State) -> dict[str, Any]:
    """Serialize state."""
    return {
        "events": [e.to_dict() for e in state.events.values()],
        "integral_l": state.integral_l,
        "model": state.model,
        "train_result": state.train_result,
        "model_stale": state.model_stale,
        "backfill_done": state.backfill_done,
        "counting_since": state.counting_since,
    }


def state_from_dict(data: dict[str, Any] | None) -> State:
    """Deserialize state."""
    if not data:
        return State()
    events = [WaterEvent.from_dict(e) for e in data.get("events", [])]
    return State(
        events={e.id: e for e in events},
        integral_l=float(data.get("integral_l", 0.0)),
        model=data.get("model"),
        train_result=data.get("train_result"),
        model_stale=bool(data.get("model_stale", False)),
        backfill_done=bool(data.get("backfill_done", False)),
        counting_since=data.get("counting_since"),
    )


def event_status(event: WaterEvent) -> str:
    """open / auto / confirmed / skipped."""
    if event.skipped:
        return STATUS_SKIPPED
    if event.label is None:
        return STATUS_OPEN
    return STATUS_CONFIRMED if event.label_source == SOURCE_USER else STATUS_AUTO


def effective_label(event: WaterEvent, categories: list[str]) -> str:
    """Category the event's liters are booked to."""
    if event.label in categories or event.label == LABEL_MIXED:
        return event.label
    return LABEL_UNCLASSIFIED


def export_events(state: State) -> dict[str, Any]:
    """Export all events as versioned JSON."""
    return {
        "version": EXPORT_VERSION,
        "events": [e.to_dict() for e in sorted(state.events.values(), key=lambda e: e.start)],
    }


def _parse_legacy(lines: list[str]) -> list[WaterEvent]:
    """Legacy JSONL: type p (flow point), event, label (latest wins)."""
    points: dict[str, list[tuple[float, float]]] = {}
    raw: dict[str, dict[str, Any]] = {}
    labels: dict[str, str] = {}
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        eid = str(rec.get("event_id", ""))
        kind = rec.get("type")
        if kind == "p":
            points.setdefault(eid, []).append((float(rec.get("t", 0)), float(rec.get("f", 0))))
        elif kind == "event":
            raw[eid] = rec
            if rec.get("label"):
                labels.setdefault(eid, rec["label"])
        elif kind == "label" and rec.get("label"):
            labels[eid] = rec["label"]
    out = []
    for eid, rec in raw.items():
        start_val = rec.get("start")
        try:
            start = float(start_val)
        except (TypeError, ValueError):
            start = datetime.fromisoformat(str(start_val)).timestamp()
        dur = float(rec.get("dauer_s", 0))
        samples = sorted(points.get(eid, []))
        helpers = {}
        if "waschmaschine" in rec:
            helpers["legacy:waschmaschine"] = 1.0 if rec["waschmaschine"] else 0.0
        label = labels.get(eid)
        out.append(
            WaterEvent(
                id=eid,
                start=start,
                end=start + dur,
                volume_l=float(rec.get("liter", 0)),
                duration_s=dur,
                max_flow=float(rec.get("max_flow", 0)),
                samples=samples,
                helpers=helpers,
                label=label,
                label_source=SOURCE_USER if label else None,
            )
        )
    return out


def import_events(state: State, data: dict[str, Any] | str) -> int:
    """Import events (v1 JSON dict/str or legacy JSONL str); returns count new/updated."""
    return merge_events(state, parse_events(data))


def parse_events(data: dict[str, Any] | str) -> list[WaterEvent]:
    """Parse an export (dict or JSON string) or legacy JSONL text."""
    events: list[WaterEvent]
    if isinstance(data, str):
        text = data.strip()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "events" in parsed:
            events = [WaterEvent.from_dict(e) for e in parsed["events"]]
        else:
            events = _parse_legacy(text.splitlines())
    else:
        events = [WaterEvent.from_dict(e) for e in data.get("events", [])]
    return events


def merge_events(state: State, events: list[WaterEvent]) -> int:
    """Merge parsed events into state without overwriting or booking."""
    n = 0
    for e in events:
        e.booked = False  # imported history is never booked onto the sensors
        old = state.events.get(e.id)
        if old is None:
            state.events[e.id] = e
            n += 1
            continue
        # existing draw: only adopt a user label, never touch volume/samples and
        # never replace an existing user label with an imported (older) one
        if e.label_source == SOURCE_USER and e.label and old.label_source != SOURCE_USER:
            old.label, old.label_source = e.label, SOURCE_USER
            n += 1
    if n:
        state.model_stale = state.model is not None
    return n
