"""Backfill from recorder history."""
from datetime import timedelta

from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from .helpers import SOURCE, set_flow, setup_entry


async def test_backfill_creates_open_events(hass, freezer):
    freezer.move_to(dt_util.utcnow() - timedelta(hours=3))
    set_flow(hass, 0.1)
    freezer.tick(timedelta(seconds=30))
    for i in range(150):  # 5 min @ 12 L/min, every 2 s
        set_flow(hass, 12.0 + (i % 2) * 0.01)
        freezer.tick(timedelta(seconds=2))
    set_flow(hass, 0.1)
    freezer.tick(timedelta(minutes=30))
    set_flow(hass, 0.11)
    await async_wait_recording_done(hass)
    freezer.move_to(dt_util.utcnow() + timedelta(hours=2))
    entry = await setup_entry(hass, backfill=True)
    await hass.async_block_till_done(wait_background_tasks=True)
    m = entry.runtime_data
    assert m.state.backfill_done
    evs = list(m.state.events.values())
    assert len(evs) == 1 and 55 < evs[0].volume_l < 65 and evs[0].label is None
    assert m.totals()["unclassified"] == 0  # history is not booked
    # runs once
    n = await m.async_backfill()
    assert n == 0


async def test_backfill_records_helper_values(hass, freezer):
    washer = "sensor.washer_power"
    freezer.move_to(dt_util.utcnow() - timedelta(hours=3))
    hass.states.async_set(washer, "45.0", {"unit_of_measurement": "W"})
    set_flow(hass, 0.1)
    freezer.tick(timedelta(seconds=30))
    for i in range(40):
        set_flow(hass, 7.3 + (i % 2) * 0.01)
        freezer.tick(timedelta(seconds=2))
    set_flow(hass, 0.1)
    freezer.tick(timedelta(minutes=30))
    set_flow(hass, 0.11)
    await async_wait_recording_done(hass)
    freezer.move_to(dt_util.utcnow() + timedelta(hours=2))
    entry = await setup_entry(hass, backfill=True, helper_entities=[washer])
    await hass.async_block_till_done(wait_background_tasks=True)
    (ev,) = entry.runtime_data.state.events.values()
    assert ev.helpers == {washer: 45.0}


async def test_missing_helper_values_are_repaired(hass, freezer):
    """Draws backfilled by an older version (no helper values) get them from history."""
    from custom_components.water_insights.models import WaterEvent

    washer = "sensor.washer_power"
    freezer.move_to(dt_util.utcnow() - timedelta(hours=3))
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(washer, "60.0")
    freezer.tick(timedelta(hours=1))
    hass.states.async_set(washer, "2.0")
    await async_wait_recording_done(hass)
    freezer.move_to(dt_util.utcnow() + timedelta(hours=2))
    entry = await setup_entry(hass, helper_entities=[washer])
    m = entry.runtime_data
    m.state.events["old"] = WaterEvent(id="old", start=t0 + 600, end=t0 + 660, volume_l=8, duration_s=60, max_flow=7)
    assert await m.async_fill_helpers() == 1
    assert m.state.events["old"].helpers == {washer: 60.0}
    assert await m.async_fill_helpers() == 0


async def test_groups_follow_recorded_appliance_run(hass, freezer):
    """Fills 50 min apart (heating phase in between) form one run from the power history."""
    from custom_components.water_insights.models import WaterEvent

    washer = "sensor.washer_power"
    freezer.move_to(dt_util.utcnow() - timedelta(hours=5))
    hass.states.async_set(washer, "2.0")
    freezer.tick(timedelta(minutes=10))
    t0 = dt_util.utcnow().timestamp()
    hass.states.async_set(washer, "60.0")
    freezer.tick(timedelta(minutes=40))
    hass.states.async_set(washer, "1800.0")
    freezer.tick(timedelta(minutes=60))
    hass.states.async_set(washer, "2.0")
    await async_wait_recording_done(hass)
    freezer.move_to(dt_util.utcnow() + timedelta(hours=2))
    rule = {"entity_id": washer, "category": "Waschmaschine", "threshold": 10.0}
    entry = await setup_entry(hass, helper_entities=[washer], appliances=[rule])
    m = entry.runtime_data
    for i, off in enumerate((60, 3060, 6060)):  # 0:01, 0:51, 1:41 after start
        m.state.events[f"f{i}"] = WaterEvent(id=f"f{i}", start=t0 + off, end=t0 + off + 60, volume_l=8,
                                             duration_s=60, max_flow=7.3, helpers={washer: 60.0})
    (g,) = await m.async_groups()
    assert g["event_ids"] == ["f0", "f1", "f2"]
    assert abs(g["start"] - t0) < 120 and abs(g["end"] - (t0 + 6000)) < 120
