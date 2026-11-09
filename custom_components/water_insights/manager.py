"""Glue between Home Assistant and the pure-Python core."""
from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .classifier import NoveltyGuard, guarded_confidence, model_from_dict
from .const import (
    CONF_APPLIANCES,
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
from .evaluation import TrainResult, predict_events, train_and_evaluate
from .features import extract
from .grouping import appliance_groups, assign_helpers, runs_from_series
from .models import DetectorConfig, WaterEvent
from .storage import State as StoredState
from .storage import effective_label, event_status, export_events, merge_events, parse_events, state_from_dict, state_to_dict

_LOGGER = logging.getLogger(__name__)
STORAGE_VERSION = 1
SAVE_DELAY = 30
TICK = timedelta(seconds=10)
PERIODIC_SAVE = timedelta(minutes=5)


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
    o.setdefault(CONF_APPLIANCES, [])
    return o


def value_from_state(state: State, mode: str, factor: float) -> tuple[float | None, float]:
    """Numeric value in L/min (flow) or L (counter) plus the factor in use.

    A missing unit keeps the last factor; an unsupported unit yields None.
    Thread-safe (State objects are immutable).
    """
    if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return None, factor
    try:
        raw = float(state.state)
    except ValueError:
        return None, factor
    unit = state.attributes.get("unit_of_measurement")
    table = FLOW_UNITS if mode == MODE_FLOW else COUNTER_UNITS
    if unit is not None:
        if unit not in table:
            return None, factor
        factor = table[unit]
    return raw * factor, factor


def detect_history(
    states: list[State], mode: str, config: DetectorConfig, until: float
) -> list[WaterEvent]:
    """Run the detector over recorded states (executor-safe)."""
    det = EventDetector(config, mode)
    factor = 1.0
    found: list[WaterEvent] = []
    for st in states:
        ts = st.last_changed.timestamp()
        if ts >= until:
            break
        value, factor = value_from_state(st, mode, factor)
        found += det.feed(ts, value)
    found += det.flush(until)
    return found


def helper_series(states: list[State]) -> list[tuple[float, float]]:
    """Recorded helper states as (timestamp, value) pairs (executor-safe)."""
    return [(st.last_changed.timestamp(), _helper_value(st)) for st in states]


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
        self.appliances: list[dict[str, Any]] = list(self.options[CONF_APPLIANCES])
        self.conf_threshold: float = float(self.options[CONF_CONF_THRESHOLD])
        self.detector_config = DetectorConfig(
            start_flow=float(self.options[CONF_START_FLOW]),
            end_quiet_s=float(self.options[CONF_END_QUIET_S]),
            min_volume_l=float(self.options[CONF_MIN_VOLUME_L]),
        )
        self.detector = EventDetector(self.detector_config, self.mode)
        self.state = StoredState()
        self._store: Store = Store(
            hass, STORAGE_VERSION, f"{DOMAIN}.{entry.entry_id}", serialize_in_event_loop=False
        )
        self._listeners: list[Callable[[], None]] = []
        self._unsubs: list[Callable[[], None]] = []
        self._helper_max: dict[str, float] = {}
        self._last_integral = 0.0
        self._factor = 1.0
        self._model_cache: tuple[int, Any, NoveltyGuard] | None = None
        self._issue_checked_at = 0
        self._enabled: set[str] = set()
        self._stopped = False

    # -- lifecycle --------------------------------------------------------
    async def async_start(self) -> None:
        """Load state and start listening."""
        self.state = state_from_dict(await self._store.async_load())
        if self.state.counting_since is None:
            self.state.counting_since = time.time()
        self._refresh_enabled()
        self.entry.async_on_unload(
            self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, self._on_ha_stop)
        )
        self._unsubs.append(
            async_track_state_change_event(
                self.hass, [self.source, *self.helpers], self._on_state_change
            )
        )
        self._unsubs.append(async_track_time_interval(self.hass, self._on_tick, TICK))
        self._unsubs.append(
            async_track_time_interval(self.hass, self._periodic_save, PERIODIC_SAVE)
        )
        if (cur := self.hass.states.get(self.source)) is not None:
            self._feed(cur, ts=time.time())
        if self.options[CONF_BACKFILL] and not self.state.backfill_done:
            self.entry.async_create_background_task(
                self.hass, self.async_backfill(), f"{DOMAIN}_backfill"
            )
        elif self._events_missing_helpers():
            self.entry.async_create_background_task(
                self.hass, self.async_fill_helpers(), f"{DOMAIN}_fill_helpers"
            )

    async def async_stop(self) -> None:
        """Stop listening, close an open draw and persist."""
        if self._stopped:
            return
        self._stopped = True
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for ev in self.detector.flush(time.time()):
            ev.helpers = dict(self._helper_max)
            self._handle_event(ev, live=True)
        self._sync_integral()
        await self._store.async_save(state_to_dict(self.state))

    async def _on_ha_stop(self, _event) -> None:
        await self.async_stop()

    def _snapshot(self) -> StoredState:
        st = self.state
        return StoredState(
            events=dict(st.events),
            integral_l=st.integral_l,
            model=st.model,
            train_result=st.train_result,
            model_stale=st.model_stale,
            backfill_done=st.backfill_done,
            counting_since=st.counting_since,
        )

    def _schedule_save(self, delay: float) -> None:
        if self._stopped:
            return
        snap = self._snapshot()
        self._store.async_delay_save(lambda: state_to_dict(snap), delay)

    def _refresh_enabled(self) -> None:
        res = self.train_result()
        self._enabled = res.enabled_classes() if res and self.state.model else set()

    @callback
    def add_listener(self, cb: Callable[[], None]) -> Callable[[], None]:
        """Register an update listener."""
        self._listeners.append(cb)
        return lambda: self._listeners.remove(cb)

    @callback
    def _notify(self, save: bool = True) -> None:
        for cb in list(self._listeners):
            cb()
        if save:
            self._schedule_save(SAVE_DELAY)

    @callback
    def _periodic_save(self, _now) -> None:
        # the flow integral changes constantly; persist it on a fixed cadence
        self._schedule_save(1)

    # -- input ------------------------------------------------------------
    def _value(self, state: State) -> float | None:
        value, self._factor = value_from_state(state, self.mode, self._factor)
        return value

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
        if done:
            self._notify(save=True)
        elif self._listeners:
            self._notify(save=False)

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
            ev.booked = True
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
        allowed = (set(self.categories) | {LABEL_MIXED}) & self._enabled
        if ev.label is None and ev.prediction in allowed and ev.confidence >= self.conf_threshold:
            ev.label = ev.prediction
            ev.label_source = SOURCE_AUTO

    # -- queries ----------------------------------------------------------
    def totals(self) -> dict[str, float]:
        """Liters per bucket for live (booked) draws."""
        out = {c: 0.0 for c in [*self.categories, LABEL_MIXED, LABEL_UNCLASSIFIED]}
        booked = 0.0
        for ev in self.state.events.values():
            if ev.booked:
                out[effective_label(ev, self.categories)] += ev.volume_l
                booked += ev.volume_l
        out[LABEL_SMALL] = max(0.0, self.state.integral_l - booked)
        return {k: round(v, 3) for k, v in out.items()}

    def period_totals(self, since_ts: float) -> dict[str, float]:
        """Liters per category for draws starting after since_ts (booked draws only)."""
        out = {c: 0.0 for c in [*self.categories, LABEL_MIXED, LABEL_UNCLASSIFIED]}
        for ev in self.state.events.values():
            if ev.booked and ev.start >= since_ts:
                out[effective_label(ev, self.categories)] += ev.volume_l
        return {k: round(v, 2) for k, v in out.items()}

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
    def async_classify_many(self, event_ids: list[str], label: str) -> int:
        """Set one user label on several draws; all ids are checked before anything changes."""
        if label not in self.categories and label != LABEL_MIXED:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="unknown_category",
                translation_placeholders={"category": label},
            )
        evs = [self._get(eid) for eid in dict.fromkeys(event_ids)]
        for ev in evs:
            ev.label, ev.label_source = label, SOURCE_USER
            self._fire_classified(ev)
        self.state.model_stale = self.state.model is not None
        self._notify()
        return len(evs)

    async def async_groups(self) -> list[dict[str, Any]]:
        """Undecided draws grouped by appliance run (see grouping.appliance_groups).

        Runs come from the appliance helper's recorded history when available.
        """
        undecided = [e for e in self.state.events.values() if e.label_source != SOURCE_USER]
        rules = [r for r in self.appliances if r.get("category") in self.categories and r.get("entity_id")]
        runs: dict[str, list[tuple[float, float]]] = {}
        if undecided and rules:
            oldest = max(min(e.start for e in undecided), time.time() - 30 * 86400)
            now = time.time()
            try:
                series = await self._helper_history(oldest, now, [r["entity_id"] for r in rules])
            except Exception:  # noqa: BLE001 - recorder unavailable: fall back to gap grouping
                _LOGGER.debug("No helper history for appliance runs", exc_info=True)
                series = {}
            for r in rules:
                if s := series.get(r["entity_id"]):
                    runs[r["entity_id"]] = runs_from_series(s, float(r.get("threshold", 10.0)), until=now)
        return appliance_groups(undecided, rules, self.categories, runs_by_entity=runs)

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
        """Train and re-score in the executor, then adopt the result."""
        events = list(self.state.events.values())
        categories, helpers = list(self.categories), list(self.helpers)
        tz = dt_util.get_default_time_zone()
        threshold = self.conf_threshold

        def job():
            result = train_and_evaluate(events, categories, helpers, tz, conf_threshold=threshold)
            todo = [e for e in events if e.label_source != SOURCE_USER]
            preds = predict_events(result.model, todo, tz) if result.ok else []
            return result, {e.id: p for e, p in zip(todo, preds)}

        result, preds = await self.hass.async_add_executor_job(job)
        if self._stopped:  # entry reloaded/unloaded meanwhile: don't touch dead state
            return result
        self.state.train_result = result.to_dict()
        if result.ok:
            self.state.model = result.model
            self.state.model_stale = False
            self._model_cache = None
            self._refresh_enabled()
            allowed = (set(self.categories) | {LABEL_MIXED}) & self._enabled
            for eid, (pred, conf) in preds.items():
                ev = self.state.events.get(eid)
                if ev is None or ev.label_source == SOURCE_USER:
                    continue
                ev.prediction, ev.confidence = pred, conf
                if pred in allowed and conf >= self.conf_threshold:
                    ev.label, ev.label_source = pred, SOURCE_AUTO
                elif ev.label_source == SOURCE_AUTO:
                    ev.label, ev.label_source = None, None
        self._notify()
        return result

    async def async_import(self, data: dict[str, Any] | str) -> int:
        """Parse in the executor, merge in the loop."""
        events = await self.hass.async_add_executor_job(parse_events, data)
        n = merge_events(self.state, events)
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
        since = self.state.counting_since or time.time()
        found = await self.hass.async_add_executor_job(
            detect_history, states.get(self.source, []), self.mode, self.detector_config, since
        )
        if self._stopped:
            return 0
        if found and self.helpers:
            series = await self._helper_history(min(e.start for e in found), end.timestamp())
            assign_helpers(found, series)
        if self._stopped:
            return 0
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

    async def _helper_history(
        self, start_ts: float, end_ts: float, entity_ids: list[str] | None = None
    ) -> dict[str, list[tuple[float, float]]]:
        """Recorded helper values between two timestamps, incl. the value in force at start."""
        from homeassistant.components.recorder import get_instance, history  # noqa: PLC0415

        start = dt_util.utc_from_timestamp(start_ts - 1)
        end = dt_util.utc_from_timestamp(end_ts)
        ids = list(dict.fromkeys(entity_ids if entity_ids is not None else self.helpers))

        def job():
            states = history.get_significant_states(
                self.hass,
                start,
                end,
                entity_ids=ids,
                include_start_time_state=True,
                significant_changes_only=False,
                no_attributes=True,
            )
            return {h: helper_series(states.get(h, [])) for h in ids}

        return await get_instance(self.hass).async_add_executor_job(job)

    def _events_missing_helpers(self) -> list[WaterEvent]:
        oldest = time.time() - 30 * 86400
        return [
            e
            for e in self.state.events.values()
            if e.start >= oldest and any(h not in e.helpers for h in self.helpers)
        ]

    async def async_fill_helpers(self) -> int:
        """Add helper values that draws are missing (older backfills, helpers added later)."""
        todo = self._events_missing_helpers()
        if not todo:
            return 0
        series = await self._helper_history(min(e.start for e in todo), time.time())
        if self._stopped:
            return 0
        n = assign_helpers(todo, series)
        if n:
            self.state.model_stale = self.state.model is not None
            self._notify()
        _LOGGER.debug("Filled helper values for %s draws of %s", n, self.source)
        return n
