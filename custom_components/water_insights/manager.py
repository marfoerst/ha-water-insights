"""Glue between Home Assistant and the pure-Python core."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .classifier import NoveltyGuard, guarded_confidence, model_from_dict
from .const import (
    CONF_BACKFILL,
    CONF_CATEGORIES,
    CONF_CONF_THRESHOLD,
    CONF_END_QUIET_S,
    CONF_HELPER_ENTITIES,
    CONF_MIN_VOLUME_L,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_START_FLOW,
    COUNTER_UNITS,
    DEFAULT_CATEGORIES,
    DEFAULT_CONF_THRESHOLD,
    DEFAULT_END_QUIET_S,
    DEFAULT_MIN_VOLUME_L,
    DEFAULT_START_FLOW,
    DOMAIN,
    EVENT_CLASSIFIED,
    EVENT_DETECTED,
    FLOW_UNITS,
    LABEL_MIXED,
    LABEL_SMALL,
    LABEL_UNCLASSIFIED,
    MODE_COUNTER,
    MODE_FLOW,
    SLOW_SENSOR_INTERVAL_S,
    SLOW_SENSOR_MIN_SAMPLES,
    SOURCE_AUTO,
    SOURCE_USER,
)
from .detector import EventDetector
from .evaluation import TrainResult, train_and_evaluate
from .features import extract
from .models import DetectorConfig, WaterEvent
from .storage import State as StoredState
from .storage import effective_label, event_status, export_events, import_events, state_from_dict, state_to_dict

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SAVE_DELAY = 30
TICK = timedelta(seconds=10)


def entry_options(entry: ConfigEntry) -> dict[str, Any]:
    """Merged data + options with defaults."""
    o = {**entry.data, **entry.options}
    o.setdefault(CONF_SOURCE_MODE, MODE_FLOW)
    o.setdefault(CONF_CATEGORIES, list(DEFAULT_CATEGORIES))
    o.setdefault(CONF_START_FLOW, DEFAULT_START_FLOW)
    o.setdefault(CONF_END_QUIET_S, DEFAULT_END_QUIET_S)
    o.setdefault(CONF_MIN_VOLUME_L, DEFAULT_MIN_VOLUME_L)
    o.setdefault(CONF_HELPER_ENTITIES, [])
    o.setdefault(CONF_CONF_THRESHOLD, DEFAULT_CONF_THRESHOLD)
    o.setdefault(CONF_BACKFILL, True)
    return o


def _helper_value(state: State | None) -> float:
    if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return math.nan
    try:
        return float(state.state)
    except ValueError:
        return 1.0 if state.state in (STATE_ON, "home", "open", "playing", "running") else 0.0


class WaterInsightsManager:
    """Owns detector, state, model for one config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize."""
        self.hass = hass
        self.entry = entry
        self.options = entry_options(entry)
        self.source: str = self.options[CONF_SOURCE_ENTITY]
        self.mode: str = self.options[CONF_SOURCE_MODE]
        self.categories: list[str] = list(self.options[CONF_CATEGORIES])
        self.helpers: list[str] = list(self.options[CONF_HELPER_ENTITIES])
        self.conf_threshold: float = float(self.options[CONF_CONF_THRESHOLD])
        self.detector_config = DetectorConfig(
            start_flow=float(self.options[CONF_START_FLOW]),
            end_quiet_s=float(self.options[CONF_END_QUIET_S]),
            min_volume_l=float(self.options[CONF_MIN_VOLUME_L]),
        )
        self.detector = EventDetector(self.detector_config, self.mode)
        self.state = StoredState()
        self._store: Store = Store(hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}")
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._helper_max: dict[str, float] = {}
        self._last_integral = 0.0
        self._factor = 1.0
        self._model_cache: tuple[int, Any, NoveltyGuard] | None = None
        self._issue_checked_at = 0

    # -- lifecycle --------------------------------------------------------
    async def async_start(self) -> None:
        """Load state and start listening."""
        self.state = state_from_dict(await self._store.async_load())
        if self.state.counting_since is None:
            self.state.counting_since = time.time()
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.source, *self.helpers], self._on_state_change
            )
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, TICK))
        if (cur := self.hass.states.get(self.source)) is not None:
            self._feed(cur, ts=time.time())
        if self.options[CONF_BACKFILL] and not self.state.backfill_done:
            self.entry.async_create_background_task(
                self.hass, self.async_backfill(), f"{DOMAIN}_backfill"
            )

    async def async_stop(self) -> None:
        """Stop listening and persist."""
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for ev in self.detector.flush(time.time()):
            self._handle_event(ev, live=True)
        self._sync_integral()
        await self._store.async_save(state_to_dict(self.state))

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register an update listener."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()
        self._store.async_delay_save(lambda: state_to_dict(self.state), SAVE_DELAY)

    # -- input ------------------------------------------------------------
    def _value(self, state: State) -> float | None:
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            return None
        try:
            raw = float(state.state)
        except ValueError:
            return None
        unit = state.attributes.get("unit_of_measurement")
        table = FLOW_UNITS if self.mode == MODE_FLOW else COUNTER_UNITS
        if unit in table:
            self._factor = table[unit]
        return raw * self._factor

    @callback
    def _on_state_change(self, event: Event[EventStateChangedData]) -> None:
        new = event.data["new_state"]
        if new is None:
            return
        if event.data["entity_id"] == self.source:
            self._feed(new)
        elif self.detector.active:
            self._track_helper(event.data["entity_id"], new)

    @callback
    def _feed(self, state: State, ts: float | None = None) -> None:
        if ts is None:
            ts = state.last_updated.timestamp()
        was_active = self.detector.active
        done = self.detector.feed(ts, self._value(state))
        if self.detector.active and not was_active:
            self._helper_max = {}
            for h in self.helpers:
                self._track_helper(h, self.hass.states.get(h))
        self._after_detector(done)
        self._check_sensor_rate()

    @callback
    def _on_tick(self, now) -> None:
        if self.detector.active:
            for h in self.helpers:
                self._track_helper(h, self.hass.states.get(h))
        self._after_detector(self.detector.tick(now.timestamp()))

    def _track_helper(self, entity_id: str, state: State | None) -> None:
        v = _helper_value(state)
        if math.isnan(v):
            return
        old = self._helper_max.get(entity_id)
        self._helper_max[entity_id] = v if old is None else max(old, v)

    def _sync_integral(self) -> None:
        delta = self.detector.integral_l - self._last_integral
        self._last_integral = self.detector.integral_l
        self.state.integral_l += delta

    @callback
    def _after_detector(self, done: list[WaterEvent]) -> None:
        self._sync_integral()
        for ev in done:
            ev.helpers = dict(self._helper_max)
            self._handle_event(ev, live=True)
        if done or self._listeners:
            self._notify()

    def _check_sensor_rate(self) -> None:
        n = self.detector.n_feeds
        if n < SLOW_SENSOR_MIN_SAMPLES or n - self._issue_checked_at < 50:
            return
        self._issue_checked_at = n
        med = self.detector.median_interval_s()
        issue_id = f"slow_sensor_{self.entry.entry_id}"
        if med is not None and med > SLOW_SENSOR_INTERVAL_S:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="slow_sensor",
                translation_placeholders={"entity": self.source, "interval": f"{med:.0f}"},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)

    # -- events -----------------------------------------------------------
    def _unique_id(self, eid: str) -> str:
        while eid in self.state.events:
            eid += "x"
        return eid

    def _handle_event(self, ev: WaterEvent, live: bool) -> None:
        ev.id = self._unique_id(ev.id)
        self._predict(ev)
        self.state.events[ev.id] = ev
        if live:
            self.state.live_volume_l += ev.volume_l
            self.hass.bus.async_fire(EVENT_DETECTED, self._event_payload(ev))

    def _event_payload(self, ev: WaterEvent) -> dict[str, Any]:
        return {
            "entry_id": self.entry.entry_id,
            "event_id": ev.id,
            "start": dt_util.utc_from_timestamp(ev.start).isoformat(),
            "end": dt_util.utc_from_timestamp(ev.end).isoformat(),
            "volume_l": round(ev.volume_l, 2),
            "duration_s": round(ev.duration_s),
            "prediction": ev.prediction,
            "confidence": None if ev.confidence is None else round(ev.confidence, 3),
            "label": ev.label,
            "status": event_status(ev),
        }

    def _model(self):
        if not self.state.model:
            return None
        key = id(self.state.model)
        if self._model_cache is None or self._model_cache[0] != key:
            model = model_from_dict(self.state.model)
            guard = NoveltyGuard.from_dict(self.state.model.get("guard", {}))
            self._model_cache = (key, model, guard)
        return self._model_cache[1], self._model_cache[2]

    def _predict(self, ev: WaterEvent) -> None:
        mg = self._model()
        if mg is None:
            return
        model, guard = mg
        X = extract(ev, self.state.model.get("helper_ids", []), dt_util.get_default_time_zone())
        try:
            _, idx, conf = guarded_confidence(model, guard, X[None, :])
        except ValueError:  # feature shape mismatch after settings change
            return
        ev.prediction = model.classes[int(idx[0])]
        ev.confidence = float(conf[0])
        enabled = set()
        if self.state.train_result:
            enabled = TrainResult.from_dict(self.state.train_result).enabled_classes()
        allowed = (set(self.categories) | {LABEL_MIXED}) & enabled
        if ev.label is None and ev.prediction in allowed and ev.confidence >= self.conf_threshold:
            ev.label = ev.prediction
            ev.label_source = SOURCE_AUTO

    # -- queries ----------------------------------------------------------
    def totals(self) -> dict[str, float]:
        """Liters per bucket for events since counting started."""
        since = self.state.counting_since or 0.0
        out = {c: 0.0 for c in [*self.categories, LABEL_MIXED, LABEL_UNCLASSIFIED]}
        for ev in self.state.events.values():
            if ev.start >= since:
                out[effective_label(ev, self.categories)] += ev.volume_l
        out[LABEL_SMALL] = max(0.0, self.state.integral_l - self.state.live_volume_l)
        return {k: round(v, 3) for k, v in out.items()}

    def events(self, status: str | None = None, limit: int = 200) -> list[WaterEvent]:
        """Events newest first, optionally filtered by status."""
        evs = sorted(self.state.events.values(), key=lambda e: e.start, reverse=True)
        if status:
            evs = [e for e in evs if event_status(e) == status]
        return evs[:limit]

    def model_status(self) -> str:
        """untrained / trained / stale."""
        if not self.state.model:
            return "untrained"
        return "stale" if self.state.model_stale else "trained"

    def train_result(self) -> TrainResult | None:
        """Last training result."""
        if not self.state.train_result:
            return None
        return TrainResult.from_dict(self.state.train_result)

    # -- commands ---------------------------------------------------------
    def _get(self, event_id: str) -> WaterEvent:
        ev = self.state.events.get(event_id)
        if ev is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_event",
                translation_placeholders={"event_id": event_id},
            )
        return ev

    def _fire_classified(self, ev: WaterEvent) -> None:
        self.hass.bus.async_fire(EVENT_CLASSIFIED, self._event_payload(ev))

    @callback
    def async_classify(self, event_id: str, label: str) -> None:
        """Set a user label."""
        if label not in self.categories and label != LABEL_MIXED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_category",
                translation_placeholders={"category": label},
            )
        ev = self._get(event_id)
        ev.label, ev.label_source = label, SOURCE_USER
        self.state.model_stale = self.state.model is not None
        self._fire_classified(ev)
        self._notify()

    @callback
    def async_confirm(self, event_id: str) -> None:
        """Confirm the current (auto) label or the model suggestion."""
        ev = self._get(event_id)
        label = ev.label or ev.prediction
        if label is None:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="nothing_to_confirm"
            )
        self.async_classify(event_id, label)

    async def async_train(self) -> TrainResult:
        """Train in the executor and adopt the result."""
        events = [WaterEvent.from_dict(e.to_dict()) for e in self.state.events.values()]
        result: TrainResult = await self.hass.async_add_executor_job(
            lambda: train_and_evaluate(
                events,
                self.categories,
                self.helpers,
                dt_util.get_default_time_zone(),
                conf_threshold=self.conf_threshold,
            )
        )
        self.state.train_result = result.to_dict()
        if result.ok:
            self.state.model = result.model
            self.state.model_stale = False
            self._model_cache = None
            for ev in self.state.events.values():  # refresh suggestions
                if ev.label_source != SOURCE_USER:
                    if ev.label_source == SOURCE_AUTO:
                        ev.label, ev.label_source = None, None
                    self._predict(ev)
        self._notify()
        return result

    @callback
    def async_import(self, data: dict[str, Any] | str) -> int:
        """Import events."""
        n = import_events(self.state, data)
        self._notify()
        return n

    def export(self) -> dict[str, Any]:
        """Export events."""
        return export_events(self.state)

    # -- backfill ---------------------------------------------------------
    async def async_backfill(self) -> int:
        """Detect past draws from recorder history (labels only, no booking)."""
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        end = dt_util.utcnow()
        start = end - timedelta(days=30)
        states = await get_instance(self.hass).async_add_executor_job(
            history.state_changes_during_period, self.hass, start, end, self.source
        )
        det = EventDetector(self.detector_config, self.mode)
        found: list[WaterEvent] = []
        since = self.state.counting_since or time.time()
        for st in states.get(self.source, []):
            ts = st.last_changed.timestamp()
            if ts >= since:
                break
            found += det.feed(ts, self._value(st))
        found += det.flush(since)
        known = {(round(e.start), round(e.volume_l)) for e in self.state.events.values()}
        n = 0
        for ev in found:
            if (round(ev.start), round(ev.volume_l)) not in known:
                self._handle_event(ev, live=False)
                n += 1
        self.state.backfill_done = True
        self._notify()
        _LOGGER.debug("Backfill found %s draws for %s", n, self.source)
        return n
