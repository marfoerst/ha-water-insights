"""Water draw detection from a flow or counter time series.

Pure Python, no Home Assistant imports. Values are fed as (timestamp, value)
where value is L/min (flow mode) or liters (counter mode), or None when the
source is unavailable.
"""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from statistics import median
from typing import Literal

from .const import MAX_SAMPLES
from .models import DetectorConfig, WaterEvent

MAX_INTERVAL_S = 3600.0  # longer silent gaps are treated as missing data
STALE_S = 900.0  # a flow value older than this is no longer extrapolated


def downsample(samples: list[tuple[float, float]], limit: int = MAX_SAMPLES):
    """Uniformly downsample a sample list to at most `limit` points."""
    if len(samples) <= limit:
        return list(samples)
    step = (len(samples) - 1) / (limit - 1)
    return [samples[round(i * step)] for i in range(limit)]


def event_id_for(ts: float) -> str:
    """Event id derived from the start time (UTC)."""
    return datetime.fromtimestamp(ts, UTC).strftime("%Y%m%dT%H%M%S")


class EventDetector:
    """Turn a sensor time series into discrete water draws."""

    def __init__(
        self, config: DetectorConfig, mode: Literal["flow", "counter"] = "flow"
    ) -> None:
        """Initialize."""
        self.config = config
        self.mode = mode
        self.integral_l = 0.0
        self._last_ts: float | None = None
        self._last_value: float | None = None  # flow (L/min) or counter (L)
        self._last_real_ts: float | None = None  # time of the last real reading
        self._intervals: deque[float] = deque(maxlen=200)
        self._n_feeds = 0
        self._reset_event()

    # -- public API -------------------------------------------------------
    @property
    def active(self) -> bool:
        """Whether a draw is currently in progress."""
        return self._start is not None

    @property
    def n_feeds(self) -> int:
        """Number of real readings fed."""
        return self._n_feeds

    def median_interval_s(self) -> float | None:
        """Median interval between real readings."""
        return median(self._intervals) if self._intervals else None

    def feed(self, ts: float, value: float | None) -> list[WaterEvent]:
        """Feed a new reading; returns draws completed by it."""
        done: list[WaterEvent] = []
        if value is not None and self._last_ts is not None and ts > self._last_ts:
            self._intervals.append(ts - self._last_ts)
        if value is not None:
            self._n_feeds += 1
            self._last_real_ts = ts

        if value is None:
            done += self._close_now()
            self._last_ts, self._last_value = ts, None
            return done

        if self.mode == "flow":
            if value > self.config.max_flow or value < 0:
                done += self._close_now()
                self._last_ts, self._last_value = ts, None
                return done
            if self._last_ts is not None and self._last_value is not None:
                done += self._interval(self._last_ts, ts, self._last_value)
            self._last_ts, self._last_value = ts, value
            return done

        # counter mode
        if self._last_ts is not None and self._last_value is not None:
            dt = ts - self._last_ts
            delta = value - self._last_value
            if dt <= 0:
                self._last_value = value
                return done
            flow = delta / dt * 60.0
            if delta < 0 or flow > self.config.max_flow or dt > MAX_INTERVAL_S:
                done += self._close_now()  # jump / reset / long gap
            else:
                done += self._interval(self._last_ts, ts, flow, volume=delta)
        self._last_ts, self._last_value = ts, value
        return done

    def tick(self, now: float) -> list[WaterEvent]:
        """Advance time without a new reading (sensor reports only changes)."""
        if self._last_ts is None or now <= self._last_ts:
            return []
        if self.mode == "flow":
            if self._last_value is None:
                return []
            limit = (self._last_real_ts or self._last_ts) + STALE_S
            if now > limit:
                # sensor silent too long: integrate up to the limit, then treat as gap
                done = self._interval(self._last_ts, limit, self._last_value) if limit > self._last_ts else []
                done += self._close_now()
                self._last_ts, self._last_value = now, None
                return done
            done = self._interval(self._last_ts, now, self._last_value)
            self._last_ts = now
            return done
        # counter: no reading means no information; only close stale draws
        if self._start is not None and now - self._last_above >= self.config.end_quiet_s:
            return self._finish()
        return []

    def flush(self, now: float) -> list[WaterEvent]:
        """Close any open draw (e.g. on shutdown)."""
        return self._close_now()

    # -- internals --------------------------------------------------------
    def _reset_event(self) -> None:
        self._start: float | None = None
        self._last_above = 0.0
        self._volume = 0.0
        self._pending = 0.0
        self._max_flow = 0.0
        self._samples: list[tuple[float, float]] = []

    def _interval(
        self, t0: float, t1: float, flow: float, volume: float | None = None
    ) -> list[WaterEvent]:
        dt = t1 - t0
        if dt <= 0:
            return []
        if dt > MAX_INTERVAL_S:
            return self._close_now()
        vol = flow * dt / 60.0 if volume is None else volume
        self.integral_l += vol
        done: list[WaterEvent] = []
        if flow >= self.config.start_flow:
            if self._start is None:
                self._start = t0
            self._volume += self._pending + vol
            self._pending = 0.0
            self._last_above = t1
            self._max_flow = max(self._max_flow, flow)
            self._samples.append((t0 - self._start, flow))
        elif self._start is not None:
            # quiet part: only the stretch within end_quiet counts as pause
            if t0 - self._last_above < self.config.end_quiet_s:
                self._samples.append((t0 - self._start, flow))
                self._pending += vol
            if t1 - self._last_above >= self.config.end_quiet_s:
                done += self._finish()
        return done

    def _close_now(self) -> list[WaterEvent]:
        if self._start is None:
            return []
        return self._finish()

    def _finish(self) -> list[WaterEvent]:
        start, end = self._start, self._last_above
        assert start is not None
        event = None
        if self._volume >= self.config.min_volume_l:
            samples = [s for s in self._samples if s[0] <= end - start]
            samples.append((end - start, 0.0))
            event = WaterEvent(
                id=event_id_for(start),
                start=start,
                end=end,
                volume_l=round(self._volume, 3),
                duration_s=round(end - start, 1),
                max_flow=round(self._max_flow, 3),
                samples=downsample(samples),
            )
        self._reset_event()
        return [event] if event else []
