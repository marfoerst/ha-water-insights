# Water Insights Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A HACS custom integration `water_insights` that detects water draws from any flow/counter sensor, lets the user label them from day 1, trains a local numpy model on demand, auto-classifies with confidence gating, and exposes per-category water sensors plus a sidebar panel.

**Architecture:** Pure-Python core (detector, features, classifier, evaluation, storage codecs) with no HA imports, fully unit-tested. A thin HA layer (`manager.py`) feeds state changes into the core, books liters, persists via `homeassistant.helpers.storage.Store`, fires bus events, and serves sensors, services, a websocket API and a vanilla-JS sidebar panel.

**Tech Stack:** Python 3.13+, Home Assistant 2026.11, numpy (bundled with HA core), pytest + pytest-homeassistant-custom-component, vanilla Web Components (no frontend build step).

**Spec:** `docs/superpowers/specs/2026-11-08-water-insights-design.md`

## Global Constraints

- Domain `water_insights`; repo `marfoerst/ha-water-insights`; MIT license; translations `de` + `en`.
- `manifest.json` `requirements: []` — only numpy/stdlib (numpy ships with HA core). No scikit-learn/scipy/torch.
- Defaults: start threshold **1.0 L/min**, end after **60 s** below threshold, minimum volume **2.0 L**, confidence threshold **0.8**, min labeled events per category **10**, min test precision for auto-classification **0.9**.
- Default categories (editable): Dusche, Toilette, Waschmaschine, Spülmaschine, Wasserhahn, Garten. Built-in, non-removable: `mixed` ("Gemischt"). Built-in sensors: Nicht eingeordnet, Gemischt, Kleinmengen.
- Category sensors: unit `L`, `device_class: water`, **`state_class: total`** (never `total_increasing`).
- Only `label_source == "user"` events are training data.
- Training runs in the executor; never on the event loop. Target: < 60 s on a Pi for a few thousand events.
- Counter-mode: negative deltas, gaps (`unknown`/`unavailable`) and implausible flows (> **60 L/min**) are discarded, never booked.
- Kleinmengen is computed from the integration's own flow integral, never from raw counter jumps.
- Fired bus events: `water_insights_event_detected`, `water_insights_event_classified`.
- No push notifications in the integration; ship an example blueprint only.
- No real household data in the repo; tests use synthetic curves.

## Review Focus

1. Source sensor goes `unavailable` mid-draw → the open draw is closed at the last good sample, no volume invented for the gap (test in Task 1).
2. Counter sensor jumps backwards/forwards by thousands of liters on reconnect → no draw, no Kleinmengen booked (test in Task 1).
3. User reclassifies an auto-classified draw → liters move between category sensors, the total stays constant, the old sensor decreases (test in Task 7).
4. User deletes/renames a category that has labeled events → events fall back to "Nicht eingeordnet", the model is marked stale, no crash (test in Task 4).
5. Training requested with too little data (< 10 per category, or < 2 qualifying categories) → clear result "not enough data" per category, no model replaced (test in Task 3).

---

### Task 1: Repo skeleton + event detector

**Files:**
- Create: `custom_components/water_insights/{__init__.py,manifest.json,const.py,models.py,detector.py}`, `hacs.json`, `LICENSE`, `requirements_test.txt`, `pyproject.toml` (pytest config), `tests/__init__.py`, `tests/conftest.py`, `tests/test_detector.py`
- `__init__.py` is a stub in this task (filled in Task 5).

**Interfaces:**
- Produces:
  - `models.WaterEvent` dataclass: `id: str` (start as `%Y%m%dT%H%M%S`), `start: float` (epoch s), `end: float`, `volume_l: float`, `duration_s: float`, `max_flow: float`, `samples: list[tuple[float, float]]` (seconds since start, L/min; capped at 400 points by uniform downsampling), `helpers: dict[str, float]`, `label: str | None`, `label_source: str | None` (`"user"`/`"auto"`/`None`), `prediction: str | None`, `confidence: float | None`, `to_dict()`, `from_dict()`.
  - `models.DetectorConfig(start_flow=1.0, end_quiet_s=60.0, min_volume_l=2.0, max_flow=60.0)`.
  - `detector.EventDetector(config, mode: Literal["flow","counter"])` with `feed(ts: float, value: float | None) -> list[WaterEvent]` (completed draws), `flush(ts) -> list[WaterEvent]`, property `integral_l: float` (all plausible volume seen incl. below threshold), property `active: bool`, `median_interval_s() -> float | None`.

- [ ] **Step 1: Write failing tests** in `tests/test_detector.py`:
  - `test_flow_shower_detected`: samples every 2 s, 0.2 L/min noise, 12 L/min for 300 s, noise → exactly one event, `abs(volume_l-60) < 1`, `abs(duration_s-300) < 4`.
  - `test_below_min_volume_ignored`: 3 L/min for 20 s → no event, but `integral_l ≈ 1.0`.
  - `test_short_pause_merges`: 8 L/min 30 s, 0 for 40 s, 8 L/min 30 s → one event (pause < 60 s).
  - `test_unavailable_closes_without_invention`: 10 L/min 60 s then `None` for 3600 s → one event ≈10 L.
  - `test_counter_mode_jump_ignored`: counter 199900 → 158368 → +5 L over 2 min → no negative/huge event, `integral_l < 10`.
  - `test_counter_mode_derives_flow`: counter rising 12 L/min, readings every 10 s for 5 min → one event ≈60 L.
  - `test_samples_capped`: 2-hour draw at 1 s → `len(samples) <= 400`.
- [ ] **Step 2:** `pytest tests/test_detector.py -q` → FAIL (module missing).
- [ ] **Step 3:** Implement. Flow volume = left Riemann sum (value × Δt/60), Δt capped at 120 s per step (longer gaps count 0). Counter mode: flow = Δvalue/Δt·60; discard Δ<0 or flow>max_flow.
- [ ] **Step 4:** tests PASS.
- [ ] **Step 5:** commit `feat: event detector with gap and jump handling`.

### Task 2: Features + synthetic generator

**Files:** Create `custom_components/water_insights/features.py`, `tools/synth.py`, `tests/test_features.py`

**Interfaces:**
- Produces: `features.BASE_FEATURES: list[str]` = `["log_volume","log_duration","log_max_flow","mean_flow","flow_cv","n_pauses","plateau_ratio","hour_sin","hour_cos","weekday_sin","weekday_cos"]`; `features.feature_names(helper_ids: list[str]) -> list[str]`; `features.extract(event: WaterEvent, helper_ids: list[str], tz: tzinfo) -> np.ndarray` (missing helper → `nan`).
- Produces: `tools/synth.py: generate(n_days: int, seed: int) -> list[WaterEvent]` with labeled draws for Dusche (8–14 L/min, 4–10 min, morning/evening), Toilette (6–9 L, ~1 min, profile ramp-down), Waschmaschine (3–6 stepped fills of 8–15 L over 60–90 min, helper `sensor.wm_power` high), Spülmaschine (3–4 short fills), Wasserhahn (2–6 L), Garten (long, 15–20 L/min); plus 5% `mixed` overlaps (shower + toilet summed).

- [ ] **Step 1:** Tests: `test_feature_vector_length` (len == 11 + helpers), `test_hour_cyclic` (23:59 and 00:01 close), `test_missing_helper_nan`, `test_synth_deterministic` (same seed → same events), `test_plateau_ratio_shower_vs_washer` (shower > washer).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS. **Step 5:** commit `feat: feature extraction and synthetic data generator`.

### Task 3: Classifier + honest evaluation

**Files:** Create `custom_components/water_insights/classifier.py`, `custom_components/water_insights/evaluation.py`, `tests/test_classifier.py`

**Interfaces:**
- Produces: `classifier.Model` with `fit(X: np.ndarray, y: list[str]) -> Model`, `predict_proba(X) -> np.ndarray` (rows sum to 1, columns = `classes: list[str]`), `to_dict()`/`from_dict()` (JSON-safe). Two implementations: `SoftmaxModel` (standardize, nan→train mean, L2 multinomial logistic regression, full-batch gradient descent) and `KnnModel` (k=7, distance-weighted, standardized).
- Produces: `evaluation.train_and_evaluate(events: list[WaterEvent], helper_ids, tz, min_per_class=10, test_fraction=0.25, min_precision=0.9, conf_threshold=0.8) -> TrainResult` where `TrainResult` has `ok: bool`, `reason: str | None` (`"not_enough_data"`), `model: dict | None` (winner refit on all data, with `type`, `helper_ids`, `classes`), `chosen: str`, `per_class: dict[str, ClassMetrics(n_train, n_test, precision, recall, auto_precision, auto_coverage, enabled)]`, `confusion: dict[str, dict[str,int]]`, `trained_at: float`.
- Split: chronological — oldest 75% train, newest 25% test, per class. Candidate selection by macro-F1 on test. `auto_precision` = precision among test predictions with confidence ≥ conf_threshold. `enabled = n_total >= min_per_class and auto_precision >= min_precision`.

- [ ] **Step 1:** Tests: `test_synthetic_main_classes_precise` (synth 60 days → Dusche/Toilette/Waschmaschine `auto_precision >= 0.9` and `enabled`), `test_not_enough_data` (5 events per class → `ok False`, `reason=="not_enough_data"`, `model is None`), `test_chronological_split` (no test event older than any train event per class), `test_model_roundtrip` (to_dict/from_dict gives identical probabilities), `test_auto_labels_excluded` (events with `label_source="auto"` don't count toward n_train).
- [ ] **Step 2:** FAIL. **Step 3:** implement. **Step 4:** PASS (and fit on 5,000 events < 10 s locally). **Step 5:** commit `feat: numpy classifiers with chronological evaluation`.

### Task 4: Storage + import/export

**Files:** Create `custom_components/water_insights/storage.py`, `tests/test_storage.py`

**Interfaces:**
- Produces: `storage.State` dataclass: `events: dict[str, WaterEvent]`, `integral_l: float`, `model: dict | None`, `train_result: dict | None`, `model_stale: bool`. Functions `state_to_dict`, `state_from_dict`, `export_events(state) -> dict` (`{"version":1,"events":[...]}`), `import_events(state, data: dict | str) -> int` (accepts v1 JSON or legacy JSONL with `type` `p`/`event`/`label`; latest label wins; legacy label strings kept verbatim), `effective_label(event, categories: list[str]) -> str` (`user/auto label` if in categories or `mixed`, else `"unclassified"`).

- [ ] **Step 1:** Tests: `test_roundtrip`, `test_import_legacy_jsonl` (fixture string with 2 events, 3 `p` lines, 2 labels for one event → latest wins, samples restored, `label_source=="user"`), `test_import_idempotent` (import twice → same count), `test_removed_category_falls_back` (label "Garten" not in categories → `"unclassified"`).
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `feat: persistent state and import/export`.

### Task 5: Manager (HA glue) + integration setup

**Files:** Modify `__init__.py`; Create `manager.py`, `tests/test_manager.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `manager.WaterInsightsManager(hass, entry)` with `async_start()`, `async_stop()`, `categories: list[str]`, `totals() -> dict[str, float]` (keys: each category, `mixed`, `unclassified`, `small`), `events(status: str | None) -> list[WaterEvent]` (status `open|auto|confirmed`), `async_classify(event_id, label)`, `async_confirm(event_id)`, `async_train() -> TrainResult`, `async_import(data) -> int`, `export() -> dict`, `add_listener(cb) -> unsub`. Stored in `entry.runtime_data`.
- Behavior: listens to source + helper state changes (`async_track_state_change_event`), feeds detector; on completed draw captures helper values (max during draw for numeric, 1/0 for on/off), predicts if model exists — sets `prediction/confidence`; if class enabled and confidence ≥ threshold → `label=prediction, label_source="auto"`. Fires `water_insights_event_detected` with `{entry_id,event_id,start,end,volume_l,duration_s,prediction,confidence,status}`; classify/confirm fire `water_insights_event_classified`. Saves via `Store` (debounced 30 s, immediate on unload). After 50 samples, if `median_interval_s() > 60` → create repair issue `slow_sensor`.
- `small` total = `integral_l − Σ event volumes` (clamped ≥ 0).

- [ ] **Step 1:** Tests (pytest-homeassistant-custom-component, `MockConfigEntry`): `test_draw_creates_event_and_bus_event`, `test_auto_classify_when_confident` (inject trained model from synth), `test_low_confidence_stays_open`, `test_persist_across_reload`, `test_slow_sensor_issue`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `feat: manager wiring detector, model and storage into HA`.

### Task 6: Config flow + options + translations

**Files:** Create `config_flow.py`, `strings.json`, `translations/en.json`, `translations/de.json`, `tests/test_config_flow.py`

**Interfaces:**
- Entry data/options keys (const.py): `source_entity`, `source_mode` (`flow|counter`, auto-detected from unit: `L/min, L/h, m³/h, gal/min` → flow; `L, m³, gal` → counter; counter m³ converted ×1000), `categories` (list[str]), `start_flow`, `end_quiet_s`, `min_volume_l`, `helper_entities` (list[str]), `conf_threshold`, `backfill` (bool, default True).
- Steps: `user` (entity selector, sensor domain) → `settings` (thresholds, categories as text list selector, helpers multi-entity selector, backfill). Abort if unit unsupported (`unsupported_unit`). Options flow = `settings` step. Unique id = source entity.

- [ ] **Step 1:** Tests: `test_flow_sensor_creates_entry`, `test_counter_sensor_detected`, `test_unsupported_unit_aborts`, `test_options_update_reloads`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `feat: config and options flow (de/en)`.

### Task 7: Sensors

**Files:** Create `sensor.py`, `tests/test_sensor.py`

**Interfaces:** one `WaterCategorySensor` per category + `mixed` + `unclassified` + `small` (unique_id `{entry_id}_{slug}`, translation keys for built-ins, name = category for user ones), `state_class TOTAL`, `device_class WATER`, unit `L`, value from `manager.totals()`; diagnostic sensors `model_status` (`untrained|trained|stale`) and `last_trained` (timestamp) with attributes per-class metrics. All under one device "Water Insights (<source name>)".

- [ ] **Step 1:** Tests: `test_sensor_attributes` (state_class total, device_class water), `test_reclassify_moves_liters` (auto Dusche 50 L → reclassify Toilette: Dusche −50, Toilette +50, sum unchanged), `test_small_sensor`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `feat: per-category water sensors`.

### Task 8: Services + websocket API

**Files:** Create `services.py`, `services.yaml`, `websocket_api.py`, `tests/test_services.py`

**Interfaces:**
- Services: `water_insights.classify_event(event_id, category)`, `confirm_event(event_id)`, `train_model(config_entry_id?)` (returns response with summary), `export_events` (response), `import_events(data)`.
- WS commands (admin): `water_insights/list` → `{entries:[{entry_id,title,categories,totals,model_status,train_result,settings}]}`; `water_insights/events` `{entry_id,status,limit}`; `water_insights/classify`, `/confirm`, `/train`, `/update_settings {entry_id, settings}` (updates options → reload).
- Unknown event id → `ServiceValidationError` with translation key `unknown_event`.

- [ ] **Step 1:** Tests: `test_classify_service`, `test_unknown_event_raises`, `test_train_service_not_enough_data`, `test_ws_list_and_events`.
- [ ] **Step 2–5:** FAIL → implement → PASS → commit `feat: services and websocket API`.

### Task 9: Recorder backfill

**Files:** Modify `manager.py`; Create `tests/test_backfill.py`

- On first setup with `backfill=True` and no stored events: read source history for the recorder window via `recorder.history.state_changes_during_period` in the recorder executor, feed a fresh detector, store resulting draws as open; integral from backfill is **not** added to `small` (avoid double-booking pre-install water). Set `backfill_done` in state.
- [ ] **Step 1:** Test `test_backfill_creates_open_events` (recorder fixture with recorded flow states → events present, status open, runs once). **Step 2–5:** FAIL → implement → PASS → commit `feat: backfill draws from recorder history`.

### Task 10: Sidebar panel

**Files:** Create `panel.py`, `frontend/water-insights-panel.js`; Modify `__init__.py` (register static path `/water_insights_static` + `panel_custom` "water-insights", sidebar title "Wasser"/"Water", icon `mdi:water`, admin only, once per HA instance).

- Vanilla custom element `water-insights-panel` receiving `hass`; uses `hass.callWS`. Tabs: **Einordnen** (open + auto-unconfirmed list: time, liters, duration, inline SVG sparkline of samples, suggestion+confidence, category `<select>`, confirm button), **Verbrauch** (totals per category today/week/month via `recorder/statistic_during_period` on the sensors), **Modell** (train button, per-class table n/precision/recall/auto-precision/enabled, confusion matrix, "braucht mehr Daten" badges), **Einstellungen** (categories, thresholds, helpers → `update_settings`). Text de/en from `hass.language`. Uses HA CSS variables, works in dark mode and at phone width.
- [ ] **Step 1:** Test `test_panel_registered` (frontend panels contain `water-insights` after setup). **Step 2:** manual smoke via `node --check frontend/water-insights-panel.js`. **Step 3–5:** implement → PASS → commit `feat: sidebar panel`.

### Task 11: Docs, examples, tools, CI, release

**Files:** Create `README.md` (en, with German section), `blueprints/automation/water_insights/notify_new_event.yaml`, `examples/dashboard.yaml`, `tools/benchmark.py` (`python tools/benchmark.py wasser_events.jsonl` → per-class metrics using `evaluation.train_and_evaluate`, prints release-criterion verdict), `.github/workflows/validate.yml` (hassfest + `hacs/action` category integration), `.github/workflows/tests.yml` (pytest), `.gitignore`.

- [ ] **Step 1:** `python tools/benchmark.py --synthetic` prints table and exits 0.
- [ ] **Step 2:** full `pytest -q` PASS; blueprint/dashboard YAML parse.
- [ ] **Step 3:** commit `docs: README, blueprint, dashboard example, CI`.
- [ ] **Step 4:** (authorized by Martin 2026-11-08) `gh repo create marfoerst/ha-water-insights --public --source . --push`, tag `v0.1.0`, `gh release create v0.1.0`; verify CI runs.
