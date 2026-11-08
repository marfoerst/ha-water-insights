"""Synthetic water-draw generator for tests and benchmarks.

Produces plausible labeled draws for a household; no real data involved.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import random

from custom_components.water_insights.detector import downsample, event_id_for
from custom_components.water_insights.models import WaterEvent

HELPER = "sensor.wm_power"
EPOCH = datetime(2026, 1, 5, tzinfo=UTC)  # a Monday


def _event(start: float, flows: list[float], step: float, label: str, wm: float):
    samples = [(i * step, f) for i, f in enumerate(flows)]
    volume = sum(f * step / 60 for f in flows)
    dur = len(flows) * step
    return WaterEvent(
        id=event_id_for(start),
        start=start,
        end=start + dur,
        volume_l=round(volume, 3),
        duration_s=round(dur, 1),
        max_flow=round(max(flows), 3),
        samples=downsample(samples + [(dur, 0.0)]),
        helpers={HELPER: wm},
        label=label,
        label_source="user",
    )


def _shower(r: random.Random) -> list[float]:
    flow = r.uniform(8, 14)
    n = int(r.uniform(4, 10) * 30)
    out = [max(0.0, flow + r.gauss(0, 0.3)) for _ in range(n)]
    if r.random() < 0.3:  # soaping pause
        p = r.randint(n // 3, 2 * n // 3)
        out[p : p + r.randint(8, 25)] = [0.1] * len(out[p : p + 25])
    return out


def _toilet(r: random.Random) -> list[float]:
    peak = r.uniform(7, 10)
    n = int(r.uniform(55, 85) / 2)
    return [max(1.2, peak * (1 - 0.6 * i / n) + r.gauss(0, 0.2)) for i in range(n)]


def _washer_fill(r: random.Random) -> list[float]:
    out: list[float] = []
    for _ in range(r.randint(2, 4)):  # valve pulses
        f = r.uniform(5, 8)
        out += [f + r.gauss(0, 0.2) for _ in range(r.randint(8, 20))]
        out += [0.2] * r.randint(2, 5)
    return out


def _dishwasher(r: random.Random) -> list[float]:
    f = r.uniform(2, 3.2)
    return [f + r.gauss(0, 0.1) for _ in range(r.randint(25, 45))]


def _tap(r: random.Random) -> list[float]:
    out: list[float] = []
    for _ in range(r.randint(1, 3)):
        f = r.uniform(3, 7)
        out += [max(1.1, f + r.gauss(0, 0.8)) for _ in range(r.randint(5, 20))]
        out += [0.3] * r.randint(1, 6)
    return out


def _garden(r: random.Random) -> list[float]:
    f = r.uniform(15, 20)
    return [f + r.gauss(0, 0.3) for _ in range(int(r.uniform(20, 60) * 30))]


def generate(n_days: int = 60, seed: int = 1) -> list[WaterEvent]:
    """Generate labeled events for n_days."""
    r = random.Random(seed)
    events: list[WaterEvent] = []

    def at(day: int, hour: float) -> float:
        return (EPOCH + timedelta(days=day, hours=hour)).timestamp()

    for d in range(n_days):
        for _ in range(r.randint(1, 3)):
            h = r.choice([r.uniform(5.5, 8), r.uniform(19, 22.5)])
            flows = _shower(r)
            if r.random() < 0.06:  # toilet flushed during shower -> mixed
                t = _toilet(r)
                o = r.randint(0, max(0, len(flows) - len(t)))
                for i, f in enumerate(t):
                    if o + i < len(flows):
                        flows[o + i] += f
                events.append(_event(at(d, h), flows, 2, "mixed", r.uniform(0, 5)))
            else:
                events.append(_event(at(d, h), flows, 2, "Dusche", r.uniform(0, 5)))
        for _ in range(r.randint(4, 8)):
            events.append(
                _event(at(d, r.uniform(6, 23.5)), _toilet(r), 2, "Toilette", r.uniform(0, 5))
            )
        for _ in range(r.randint(3, 8)):
            events.append(
                _event(at(d, r.uniform(6, 23)), _tap(r), 2, "Wasserhahn", r.uniform(0, 5))
            )
        if d % 3 == 0:
            h = r.uniform(9, 16)
            for k in range(r.randint(4, 6)):
                events.append(
                    _event(
                        at(d, h + k * 0.25),
                        _washer_fill(r),
                        2,
                        "Waschmaschine",
                        r.uniform(150, 2200),
                    )
                )
        if d % 2 == 1:
            h = r.uniform(19.5, 22)
            for k in range(r.randint(3, 4)):
                events.append(
                    _event(at(d, h + k * 0.3), _dishwasher(r), 2, "Spülmaschine", r.uniform(0, 5))
                )
        if r.random() < 0.15:
            events.append(_event(at(d, r.uniform(18, 21)), _garden(r), 2, "Garten", r.uniform(0, 5)))
    events.sort(key=lambda e: e.start)
    # make ids unique
    seen: set[str] = set()
    for e in events:
        while e.id in seen:
            e.id += "x"
        seen.add(e.id)
    return events
