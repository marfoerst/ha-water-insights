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


async def test_update_settings_rejects_invalid(hass, hass_ws_client):
    entry = await setup_entry(hass)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "water_insights/update_settings", "entry_id": entry.entry_id,
                        "settings": {"start_flow": 0}})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["message"] == "invalid_settings"
    assert entry.options["start_flow"] == 1.0


async def test_import_from_path(hass, tmp_path):
    import json

    entry = await setup_entry(hass)
    f = tmp_path / "export.json"
    f.write_text(json.dumps({"version": 1, "events": [e.to_dict() for e in generate(3, seed=2)]}))
    hass.config.allowlist_external_dirs = {str(tmp_path)}
    r = await hass.services.async_call(DOMAIN, "import_events", {"path": str(f)}, blocking=True, return_response=True)
    assert r["imported"] > 10
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "import_events", {"path": "/etc/passwd"}, blocking=True, return_response=True)


async def test_train_during_reload_keeps_new_state(hass):
    entry = await setup_entry(hass)
    old = entry.runtime_data
    old.state.events.update({e.id: e for e in generate(40, seed=2)})
    task = hass.async_create_task(old.async_train())
    await old.async_stop()
    await task
    assert old.state.model is None  # dead manager did not adopt


async def test_removing_entry_deletes_storage(hass, hass_storage, freezer):
    entry = await setup_entry(hass)
    await simulate_shower(hass, freezer)
    await hass.config_entries.async_unload(entry.entry_id)
    assert f"water_insights.{entry.entry_id}" in hass_storage
    await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert f"water_insights.{entry.entry_id}" not in hass_storage


WM = "sensor.washer_power"


def _inject(m, n, power, start=1_000_000.0, step=600.0, prefix="wm"):
    from custom_components.water_insights.models import WaterEvent

    ids = []
    for i in range(n):
        e = WaterEvent(id=f"{prefix}{i}", start=start + i * step, end=start + i * step + 60, volume_l=8.0,
                       duration_s=60, max_flow=7.3, helpers={WM: power})
        m.state.events[e.id] = e
        ids.append(e.id)
    return ids


async def test_ws_classify_many(hass, hass_ws_client):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    ids = _inject(m, 3, 60.0)
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "water_insights/classify_many", "entry_id": entry.entry_id,
                        "event_ids": ids[:2], "category": "Waschmaschine"})
    assert (await ws.receive_json())["success"]
    assert [m.state.events[i].label for i in ids] == ["Waschmaschine", "Waschmaschine", None]
    assert m.state.events[ids[0]].label_source == "user"
    # all-or-nothing: one unknown id rejects the whole batch
    await ws.send_json({"id": 2, "type": "water_insights/classify_many", "entry_id": entry.entry_id,
                        "event_ids": [ids[2], "nope"], "category": "Toilette"})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["message"] == "unknown_event"
    assert m.state.events[ids[2]].label is None


async def test_ws_groups_from_appliance_rule(hass, hass_ws_client):
    entry = await setup_entry(hass, helper_entities=[WM],
                              appliances=[{"entity_id": WM, "category": "Waschmaschine", "threshold": 10}])
    m = entry.runtime_data
    _inject(m, 4, 60.0)
    _inject(m, 2, 2.0, start=2_000_000.0, prefix="idle")  # machine idle: no group
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "water_insights/groups", "entry_id": entry.entry_id})
    r = await ws.receive_json()
    assert r["success"]
    (g,) = r["result"]["groups"]
    assert g["category"] == "Waschmaschine" and len(g["event_ids"]) == 4 and len(g["preselected"]) == 4
    await ws.send_json({"id": 2, "type": "water_insights/list"})
    s = (await ws.receive_json())["result"]["entries"][0]["settings"]
    assert s["appliances"] == [{"entity_id": WM, "category": "Waschmaschine", "threshold": 10}]


async def test_ws_update_settings_appliances(hass, hass_ws_client):
    entry = await setup_entry(hass, helper_entities=[WM])
    ws = await hass_ws_client(hass)
    rule = {"entity_id": WM, "category": "Waschmaschine", "threshold": 15}
    await ws.send_json({"id": 1, "type": "water_insights/update_settings", "entry_id": entry.entry_id,
                        "settings": {"appliances": [rule]}})
    assert (await ws.receive_json())["success"]
    await hass.async_block_till_done()
    assert entry.options["appliances"] == [{**rule, "threshold": 15.0}]
    await ws.send_json({"id": 2, "type": "water_insights/update_settings", "entry_id": entry.entry_id,
                        "settings": {"appliances": [{"entity_id": WM, "category": "Pool"}]}})
    r = await ws.receive_json()
    assert not r["success"] and r["error"]["message"] == "invalid_settings"


async def test_panel_survives_reload_and_goes_with_last_entry(hass):
    entry = await setup_entry(hass)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert "water-insights" in hass.data["frontend_panels"]  # open browser tabs keep their page
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert "water-insights" not in hass.data["frontend_panels"]


async def test_ws_and_services_reject_skip(hass, hass_ws_client):
    entry = await setup_entry(hass)
    m = entry.runtime_data
    ids = _inject(m, 3, 60.0)
    m.state.events[ids[0]].prediction = "Toilette"
    m.state.events[ids[0]].confidence = 0.7
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 1, "type": "water_insights/reject", "entry_id": entry.entry_id, "event_ids": [ids[0]]})
    assert (await ws.receive_json())["success"]
    assert m.state.events[ids[0]].rejected == ["Toilette"]
    await ws.send_json({"id": 2, "type": "water_insights/skip", "entry_id": entry.entry_id, "event_ids": ids[1:]})
    assert (await ws.receive_json())["success"]
    await ws.send_json({"id": 3, "type": "water_insights/list"})
    assert (await ws.receive_json())["result"]["entries"][0]["counts"]["skipped"] == 2
    await ws.send_json({"id": 4, "type": "water_insights/events", "entry_id": entry.entry_id, "status": "skipped"})
    assert len((await ws.receive_json())["result"]["events"]) == 2
    await hass.services.async_call(DOMAIN, "classify_event", {"event_id": ids[1], "category": "Toilette"}, blocking=True)
    assert not m.state.events[ids[1]].skipped
    await hass.services.async_call(DOMAIN, "skip_event", {"event_id": ids[1]}, blocking=True)
    assert m.state.events[ids[1]].skipped and m.state.events[ids[1]].label is None
    with pytest.raises(ServiceValidationError):  # nothing to reject on a draw without suggestion
        await hass.services.async_call(DOMAIN, "reject_event", {"event_id": ids[2]}, blocking=True)
