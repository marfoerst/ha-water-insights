"""Sensors."""
from homeassistant.helpers import entity_registry as er

from .helpers import setup_entry, simulate_shower, trained_state


def _sensor(hass, entry, key):
    reg = er.async_get(hass)
    eid = reg.async_get_entity_id("sensor", "water_insights", f"{entry.entry_id}_{key}")
    return hass.states.get(eid)


async def test_sensor_attributes(hass):
    entry = await setup_entry(hass)
    st = _sensor(hass, entry, "dusche")
    assert st.attributes["state_class"] == "total"
    assert st.attributes["device_class"] == "water"
    assert st.attributes["unit_of_measurement"] == "L"
    assert _sensor(hass, entry, "small") is not None
    assert _sensor(hass, entry, "model_status").state == "untrained"


async def test_reclassify_moves_liters(hass, freezer):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    trained_state(m)
    await simulate_shower(hass, freezer)
    ev = next(iter(m.state.events.values()))
    assert ev.label == "Dusche"
    before = {k: float(_sensor(hass, entry, k).state) for k in ("dusche", "toilette")}
    total_before = sum(m.totals().values())
    m.async_classify(ev.id, "Toilette")
    await hass.async_block_till_done()
    after = {k: float(_sensor(hass, entry, k).state) for k in ("dusche", "toilette")}
    assert after["dusche"] == before["dusche"] - ev.volume_l
    assert abs(after["toilette"] - (before["toilette"] + ev.volume_l)) < 0.01
    assert abs(sum(m.totals().values()) - total_before) < 0.01
    assert _sensor(hass, entry, "model_status").state == "stale"


async def test_small_sensor(hass, freezer):
    from datetime import timedelta

    from .helpers import set_flow

    entry = await setup_entry(hass)
    set_flow(hass, 0.0)
    freezer.tick(timedelta(seconds=5))
    set_flow(hass, 0.5)  # below start threshold
    freezer.tick(timedelta(seconds=120))
    set_flow(hass, 0.0)
    await hass.async_block_till_done()
    assert abs(entry.runtime_data.totals()["small"] - 1.0) < 0.05
    assert entry.runtime_data.state.events == {}


async def test_removed_category_falls_back(hass, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    m = entry.runtime_data
    ev = next(iter(m.state.events.values()))
    m.async_classify(ev.id, "Garten")
    hass.config_entries.async_update_entry(entry, options={**entry.options, "categories": ["Dusche"]})
    await hass.async_block_till_done()
    m = entry.runtime_data
    assert m.totals()["unclassified"] == ev.volume_l
    assert "Garten" not in m.totals()
