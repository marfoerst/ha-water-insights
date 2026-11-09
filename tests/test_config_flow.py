"""Config and options flow."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType

from custom_components.water_insights.const import DOMAIN

from .helpers import SOURCE, set_flow, setup_entry

SETTINGS = {
    "categories": ["Dusche", " Toilette ", "Dusche", "mixed", ""],
    "start_flow": 1.0,
    "end_quiet_s": 60,
    "min_volume_l": 2.0,
    "helper_entities": [],
    "conf_threshold": 0.8,
    "backfill": False,
}


async def _start(hass, entity):
    r = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    return await hass.config_entries.flow.async_configure(r["flow_id"], {"source_entity": entity})


async def test_flow_sensor_creates_entry(hass):
    set_flow(hass, 0.1)
    r = await _start(hass, SOURCE)
    assert r["step_id"] == "settings"
    r = await hass.config_entries.flow.async_configure(r["flow_id"], SETTINGS)
    assert r["type"] is FlowResultType.CREATE_ENTRY
    assert r["data"] == {"source_entity": SOURCE, "source_mode": "flow"}
    assert r["options"]["categories"] == ["Dusche", "Toilette"]
    await hass.async_block_till_done()


async def test_counter_sensor_detected(hass):
    hass.states.async_set("sensor.meter", 1234.5, {"unit_of_measurement": "m³"})
    r = await _start(hass, "sensor.meter")
    r = await hass.config_entries.flow.async_configure(r["flow_id"], SETTINGS)
    assert r["data"]["source_mode"] == "counter"
    await hass.async_block_till_done()


async def test_unsupported_unit_errors(hass):
    hass.states.async_set("sensor.temp", 20, {"unit_of_measurement": "°C"})
    r = await _start(hass, "sensor.temp")
    assert r["type"] is FlowResultType.FORM and r["errors"] == {"source_entity": "unsupported_unit"}


async def test_duplicate_aborts(hass):
    set_flow(hass, 0.1)
    await setup_entry(hass)
    r = await _start(hass, SOURCE)
    assert r["type"] is FlowResultType.ABORT and r["reason"] == "already_configured"


async def test_options_update_reloads(hass):
    set_flow(hass, 0.1)
    entry = await setup_entry(hass)
    r = await hass.config_entries.options.async_init(entry.entry_id)
    opts = {k: v for k, v in SETTINGS.items() if k != "backfill"} | {"categories": ["Bad", "Küche"]}
    r = await hass.config_entries.options.async_configure(r["flow_id"], opts)
    assert r["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.categories == ["Bad", "Küche"]
    assert hass.states.get("sensor.water_insights_water_flow_bad") is not None


async def test_options_flow_keeps_appliance_rules(hass):
    set_flow(hass, 0.1)
    rule = {"entity_id": "sensor.washer_power", "category": "Waschmaschine", "threshold": 10.0}
    entry = await setup_entry(hass, appliances=[rule])
    r = await hass.config_entries.options.async_init(entry.entry_id)
    opts = {k: v for k, v in SETTINGS.items() if k != "backfill"} | {"categories": ["Waschmaschine"]}
    await hass.config_entries.options.async_configure(r["flow_id"], opts)
    await hass.async_block_till_done()
    assert entry.options["appliances"] == [rule]
