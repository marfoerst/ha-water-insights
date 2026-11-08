"""Services and websocket API."""
import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.water_insights.const import DOMAIN
from tools.synth import generate

from .helpers import setup_entry, simulate_shower


async def test_classify_service(hass, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    eid = next(iter(entry.runtime_data.state.events))
    await hass.services.async_call(DOMAIN, "classify_event", {"event_id": eid, "category": "Dusche"}, blocking=True)
    ev = entry.runtime_data.state.events[eid]
    assert ev.label == "Dusche" and ev.label_source == "user"


async def test_unknown_event_raises(hass):
    await setup_entry(hass)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "confirm_event", {"event_id": "nope"}, blocking=True)


async def test_unknown_category_raises(hass, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    eid = next(iter(entry.runtime_data.state.events))
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "classify_event", {"event_id": eid, "category": "Pool"}, blocking=True)


async def test_train_service_not_enough_data(hass):
    await setup_entry(hass)
    r = await hass.services.async_call(DOMAIN, "train_model", {}, blocking=True, return_response=True)
    assert r["ok"] is False and r["reason"] == "not_enough_data"


async def test_import_train_export(hass):
    entry = await setup_entry(hass)
    data = {"version": 1, "events": [e.to_dict() for e in generate(40, seed=2)]}
    r = await hass.services.async_call(DOMAIN, "import_events", {"data": data}, blocking=True, return_response=True)
    assert r["imported"] > 100
    r = await hass.services.async_call(DOMAIN, "train_model", {}, blocking=True, return_response=True)
    assert r["ok"] and "Dusche" in r["auto_classes"]
    out = await hass.services.async_call(DOMAIN, "export_events", {}, blocking=True, return_response=True)
    assert len(out["events"]) == len(entry.runtime_data.state.events)
    # imported history must not be booked onto the sensors
    assert entry.runtime_data.totals()["Dusche"] == 0


async def test_ws_list_and_events(hass, hass_ws_client, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "water_insights/list"})
    r = await ws.receive_json()
    assert r["success"] and r["result"]["entries"][0]["counts"]["open"] == 1
    await ws.send_json({"id": 2, "type": "water_insights/events", "entry_id": entry.entry_id, "status": "open"})
    r = await ws.receive_json()
    ev = r["result"]["events"][0]
    await ws.send_json({"id": 3, "type": "water_insights/classify", "entry_id": entry.entry_id,
                        "event_id": ev["id"], "category": "Dusche"})
    assert (await ws.receive_json())["success"]
    await ws.send_json({"id": 4, "type": "water_insights/confirm", "entry_id": entry.entry_id, "event_id": "x"})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["message"] == "unknown_event"
    await ws.send_json({"id": 5, "type": "water_insights/update_settings", "entry_id": entry.entry_id,
                        "settings": {"min_volume_l": 3.0}})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["min_volume_l"] == 3.0


async def test_panel_registered(hass):
    await setup_entry(hass)
    assert "water-insights" in hass.data["frontend_panels"]
