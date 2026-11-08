"""Test helpers."""
from __future__ import annotations

from datetime import UTC

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.water_insights.const import DOMAIN
from custom_components.water_insights.evaluation import train_and_evaluate
from tools.synth import HELPER, generate

SOURCE = "sensor.water_flow"
CATS = ["Dusche", "Toilette", "Wasserhahn", "Waschmaschine", "Spülmaschine", "Garten"]


def set_flow(hass: HomeAssistant, value, unit="L/min", entity=SOURCE):
    hass.states.async_set(entity, value, {"unit_of_measurement": unit})


async def setup_entry(hass: HomeAssistant, **options) -> MockConfigEntry:
    opts = {
        "categories": CATS,
        "start_flow": 1.0,
        "end_quiet_s": 60.0,
        "min_volume_l": 2.0,
        "helper_entities": [],
        "conf_threshold": 0.8,
        "backfill": False,
        **options,
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Water flow",
        data={"source_entity": SOURCE, "source_mode": "flow"},
        options=opts,
        unique_id=SOURCE,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def trained_state(manager):
    """Give the manager a model trained on synthetic data (no helpers)."""
    res = train_and_evaluate(generate(60, seed=1), CATS, [], UTC)
    manager.state.model = res.model
    manager.state.train_result = res.to_dict()
    manager._model_cache = None
    manager._refresh_enabled()
    return res


async def simulate_shower(hass, freezer, flow=12.0, seconds=300, step=2):
    """Drive the source sensor through a shower-like draw, then quiet."""
    from datetime import timedelta

    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    set_flow(hass, 0.1)
    await hass.async_block_till_done()
    freezer.tick(timedelta(seconds=10))
    for i in range(seconds // step):
        set_flow(hass, round(flow + (0.01 if i % 2 else 0), 2))
        freezer.tick(timedelta(seconds=step))
    set_flow(hass, 0.1)
    await hass.async_block_till_done()
    for _ in range(10):
        freezer.tick(timedelta(seconds=15))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
