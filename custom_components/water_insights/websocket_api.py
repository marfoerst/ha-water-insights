"""Websocket commands for the sidebar panel."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ServiceValidationError

from .const import (
    CONF_CATEGORIES,
    CONF_CONF_THRESHOLD,
    CONF_END_QUIET_S,
    CONF_HELPER_ENTITIES,
    CONF_MIN_VOLUME_L,
    CONF_START_FLOW,
    DOMAIN,
)
from .services import managers, resolve, train_summary
from .storage import event_status

SETTING_KEYS = (
    CONF_CATEGORIES,
    CONF_START_FLOW,
    CONF_END_QUIET_S,
    CONF_MIN_VOLUME_L,
    CONF_HELPER_ENTITIES,
    CONF_CONF_THRESHOLD,
)


def _entry_info(m) -> dict[str, Any]:
    res = m.train_result()
    return {
        "entry_id": m.entry.entry_id,
        "title": m.entry.title,
        "source": m.source,
        "mode": m.mode,
        "categories": m.categories,
        "totals": m.totals(),
        "model_status": m.model_status(),
        "train_result": res.to_dict() | {"model": None} if res else None,
        "settings": {k: m.options.get(k) for k in SETTING_KEYS},
        "counts": {
            s: sum(1 for e in m.state.events.values() if event_status(e) == s)
            for s in ("open", "auto", "confirmed")
        },
    }


def _event_info(e) -> dict[str, Any]:
    d = e.to_dict()
    d["status"] = event_status(e)
    return d


def _wrap(handler):
    async def inner(hass, connection, msg):
        try:
            result = handler(hass, msg)
            if hasattr(result, "__await__"):
                result = await result
            connection.send_result(msg["id"], result)
        except ServiceValidationError as err:
            connection.send_error(msg["id"], "invalid", str(err.translation_key or err))

    return inner


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    """Register commands."""

    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/list"})
    @websocket_api.require_admin
    @websocket_api.async_response
    async def ws_list(hass, connection, msg):
        connection.send_result(msg["id"], {"entries": [_entry_info(m) for m in managers(hass)]})

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/events",
            vol.Required("entry_id"): str,
            vol.Optional("status"): vol.In(["open", "auto", "confirmed"]),
            vol.Optional("limit", default=200): vol.All(int, vol.Range(1, 2000)),
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    @_wrap
    def ws_events(hass, msg):
        m = resolve(hass, msg["entry_id"])
        return {"events": [_event_info(e) for e in m.events(msg.get("status"), msg["limit"])]}

    @websocket_api.websocket_command(
        {
            vol.Required("type"): f"{DOMAIN}/classify",
            vol.Required("entry_id"): str,
            vol.Required("event_id"): str,
            vol.Required("category"): str,
        }
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    @_wrap
    def ws_classify(hass, msg):
        resolve(hass, msg["entry_id"]).async_classify(msg["event_id"], msg["category"])
        return {}

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{DOMAIN}/confirm", vol.Required("entry_id"): str, vol.Required("event_id"): str}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    @_wrap
    def ws_confirm(hass, msg):
        resolve(hass, msg["entry_id"]).async_confirm(msg["event_id"])
        return {}

    @websocket_api.websocket_command({vol.Required("type"): f"{DOMAIN}/train", vol.Required("entry_id"): str})
    @websocket_api.require_admin
    @websocket_api.async_response
    @_wrap
    async def ws_train(hass, msg):
        return train_summary(await resolve(hass, msg["entry_id"]).async_train())

    @websocket_api.websocket_command(
        {vol.Required("type"): f"{DOMAIN}/update_settings", vol.Required("entry_id"): str, vol.Required("settings"): dict}
    )
    @websocket_api.require_admin
    @websocket_api.async_response
    @_wrap
    def ws_update(hass, msg):
        from .config_flow import clean_categories  # noqa: PLC0415

        m = resolve(hass, msg["entry_id"])
        new = {k: v for k, v in msg["settings"].items() if k in SETTING_KEYS}
        if CONF_CATEGORIES in new:
            new[CONF_CATEGORIES] = clean_categories(new[CONF_CATEGORIES])
            if not new[CONF_CATEGORIES]:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="no_categories")
        hass.config_entries.async_update_entry(m.entry, options={**m.entry.options, **new})
        return {}

    for cmd in (ws_list, ws_events, ws_classify, ws_confirm, ws_train, ws_update):
        websocket_api.async_register_command(hass, cmd)
