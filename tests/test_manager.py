"""Manager behaviour inside Home Assistant."""
from __future__ import annotations

import pytest

from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import issue_registry as ir

from custom_components.water_insights.const import EVENT_DETECTED
from pytest_homeassistant_custom_component.common import async_capture_events

from .helpers import SOURCE, set_flow, setup_entry, simulate_shower, trained_state


async def test_draw_creates_event_and_bus_event(hass, freezer):
    entry = await setup_entry(hass)
    events = async_capture_events(hass, EVENT_DETECTED)
    await simulate_shower(hass, freezer)
    m = entry.runtime_data
    assert len(m.state.events) == 1
    ev = next(iter(m.state.events.values()))
    assert 55 < ev.volume_l < 65
    assert len(events) == 1 and events[0].data["status"] == "open"
    assert m.totals()["unclassified"] == ev.volume_l


async def test_auto_classify_when_confident(hass, freezer):
    entry = await setup_entry(hass)
    trained_state(entry.runtime_data)
    await simulate_shower(hass, freezer)
    ev = next(iter(entry.runtime_data.state.events.values()))
    assert ev.prediction == "Dusche"
    assert ev.label == "Dusche" and ev.label_source == "auto"


async def test_low_confidence_stays_open(hass, freezer):
    entry = await setup_entry(hass, conf_threshold=0.999)
    trained_state(entry.runtime_data)
    entry.runtime_data.conf_threshold = 1.01
    await simulate_shower(hass, freezer)
    ev = next(iter(entry.runtime_data.state.events.values()))
    assert ev.prediction is not None and ev.label is None


async def test_unfamiliar_draw_stays_open(hass, freezer):
    entry = await setup_entry(hass)
    trained_state(entry.runtime_data)
    await simulate_shower(hass, freezer, flow=45.0, seconds=1200)  # nothing like it trained
    ev = next(iter(entry.runtime_data.state.events.values()))
    assert ev.label is None and ev.confidence == 0.0


async def test_persist_across_reload(hass, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert len(entry.runtime_data.state.events) == 1


async def test_slow_sensor_issue(hass, freezer):
    from datetime import timedelta

    await setup_entry(hass)
    for i in range(110):
        set_flow(hass, 0.1 + (i % 2) * 0.01)
        freezer.tick(timedelta(seconds=300))
    await hass.async_block_till_done()
    reg = ir.async_get(hass)
    assert any(i.translation_key == "slow_sensor" for i in reg.issues.values())


async def test_unavailable_source_no_invention(hass, freezer):
    from datetime import timedelta

    entry = await setup_entry(hass)
    set_flow(hass, 10.0)
    freezer.tick(timedelta(seconds=60))
    set_flow(hass, 10.01)
    freezer.tick(timedelta(seconds=2))
    hass.states.async_set(SOURCE, "unavailable")
    await hass.async_block_till_done()
    freezer.tick(timedelta(hours=2))
    set_flow(hass, 0.0)
    await hass.async_block_till_done()
    evs = list(entry.runtime_data.state.events.values())
    assert len(evs) == 1 and evs[0].volume_l < 11


async def test_state_saved_while_sensor_keeps_reporting(hass, freezer, hass_storage):
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    for i in range(40):  # sensor keeps reporting every 2 s for > 1 min
        set_flow(hass, 0.1 + (i % 2) * 0.01)
        freezer.tick(timedelta(seconds=2))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    key = f"water_insights.{entry.entry_id}"
    assert key in hass_storage and len(hass_storage[key]["data"]["events"]) == 1


async def test_ha_stop_flushes_open_draw(hass, freezer, hass_storage):
    from datetime import timedelta

    from homeassistant.const import EVENT_HOMEASSISTANT_STOP

    entry = await setup_entry(hass)
    set_flow(hass, 0.1)
    freezer.tick(timedelta(seconds=5))
    for i in range(30):
        set_flow(hass, 9.0 + (i % 2) * 0.01)
        freezer.tick(timedelta(seconds=2))
    await hass.async_block_till_done()
    hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
    await hass.async_block_till_done()
    data = hass_storage[f"water_insights.{entry.entry_id}"]["data"]
    assert len(data["events"]) == 1 and data["events"][0]["booked"]


async def test_unsupported_unit_ignored(hass, freezer):
    from datetime import timedelta

    entry = await setup_entry(hass)
    set_flow(hass, 0.0)
    freezer.tick(timedelta(seconds=5))
    for i in range(60):
        set_flow(hass, 500 + i, unit="ml/s")
        freezer.tick(timedelta(seconds=2))
    set_flow(hass, 0.0)
    await hass.async_block_till_done()
    assert entry.runtime_data.state.integral_l < 0.1


async def test_reject_auto_label_and_never_reapply(hass, freezer):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    trained_state(m)
    await simulate_shower(hass, freezer)
    ev = next(iter(m.state.events.values()))
    assert ev.label == "Dusche" and ev.label_source == "auto"
    m.async_reject([ev.id])
    assert ev.label is None and ev.prediction is None and ev.rejected == ["Dusche"]
    assert m.events("open")[0].id == ev.id
    assert m.totals()["Dusche"] == 0 and m.totals()["unclassified"] > 50
    # retraining/re-scoring must not bring the rejected category back
    m._predict(ev)
    assert ev.label is None and ev.prediction is None


async def test_reject_open_suggestion(hass, freezer):
    entry = await setup_entry(hass, conf_threshold=0.99)
    m = entry.runtime_data
    trained_state(m)
    await simulate_shower(hass, freezer, flow=6.0, seconds=60)
    ev = next(iter(m.state.events.values()))
    assert ev.label is None and ev.prediction is not None
    sugg = ev.prediction
    m.async_reject([ev.id])
    assert ev.prediction is None and ev.rejected == [sugg]


async def test_reject_user_label_raises(hass, freezer):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    await simulate_shower(hass, freezer)
    eid = next(iter(m.state.events))
    m.async_classify(eid, "Dusche")
    with pytest.raises(ServiceValidationError):
        m.async_reject([eid])


async def test_skip_and_undo_by_classifying(hass, freezer):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    trained_state(m)
    await simulate_shower(hass, freezer)
    ev = next(iter(m.state.events.values()))
    m.async_skip([ev.id])
    assert ev.skipped and ev.label is None
    assert m.events("open") == [] and m.events("skipped")[0].id == ev.id
    assert m.totals()["unclassified"] > 50  # liters stay visible as unclassified
    m._predict(ev)
    assert ev.label is None  # a skipped draw is never auto-labeled again
    m.async_classify(ev.id, "Dusche")
    assert not ev.skipped and ev.label == "Dusche"
