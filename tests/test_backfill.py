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
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m.state.backfill_done
    evs = list(m.state.events.values())
    assert len(evs) == 1 and 55 < evs[0].volume_l < 65 and evs[0].label is None
    assert m.totals()["unclassified"] == 0  # history is not booked
    # runs once
    n = await m.async_backfill()
    assert n == 0
