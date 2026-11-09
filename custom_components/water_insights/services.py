"""Service actions."""
from __future__ import annotations

from typing import TYPE_CHECKING

import voluptuous as vol

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import DOMAIN

if TYPE_CHECKING:
    from .manager import WaterInsightsManager

ATTR_ENTRY = "config_entry_id"
ATTR_EVENT = "event_id"
ATTR_CATEGORY = "category"
ATTR_DATA = "data"
ATTR_PATH = "path"


def managers(hass: HomeAssistant) -> list[WaterInsightsManager]:
    """All loaded managers."""
    return [e.runtime_data for e in hass.config_entries.async_loaded_entries(DOMAIN)]


def resolve(hass: HomeAssistant, entry_id: str | None, event_id: str | None = None) -> WaterInsightsManager:
    """Find the manager for an entry id or an event id."""
    ms = managers(hass)
    if entry_id:
        for m in ms:
            if m.entry.entry_id == entry_id:
                return m
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="unknown_entry")
    if event_id:
        for m in ms:
            if event_id in m.state.events:
                return m
        raise ServiceValidationError(
            translation_domain=DOMAIN,
            translation_key="unknown_event",
            translation_placeholders={"event_id": event_id},
        )
    if len(ms) == 1:
        return ms[0]
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")


def train_summary(result) -> dict:
    """Compact, JSON-safe training summary."""
    return {
        "ok": result.ok,
        "reason": result.reason,
        "model": result.chosen,
        "auto_classes": sorted(result.enabled_classes()),
        "per_class": {
            c: {"n": m.n_total, "auto_precision": m.auto_precision, "enabled": m.enabled}
            for c, m in result.per_class.items()
        },
    }


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register services."""

    async def classify(call: ServiceCall) -> None:
        eid = call.data[ATTR_EVENT]
        resolve(hass, call.data.get(ATTR_ENTRY), eid).async_classify(eid, call.data[ATTR_CATEGORY])

    async def confirm(call: ServiceCall) -> None:
        eid = call.data[ATTR_EVENT]
        resolve(hass, call.data.get(ATTR_ENTRY), eid).async_confirm(eid)

    async def reject(call: ServiceCall) -> None:
        eid = call.data[ATTR_EVENT]
        resolve(hass, call.data.get(ATTR_ENTRY), eid).async_reject([eid])

    async def skip(call: ServiceCall) -> None:
        eid = call.data[ATTR_EVENT]
        resolve(hass, call.data.get(ATTR_ENTRY), eid).async_skip([eid])

    async def train(call: ServiceCall) -> ServiceResponse:
        result = await resolve(hass, call.data.get(ATTR_ENTRY)).async_train()
        return train_summary(result)

    async def export(call: ServiceCall) -> ServiceResponse:
        return resolve(hass, call.data.get(ATTR_ENTRY)).export()

    async def import_(call: ServiceCall) -> ServiceResponse:
        data = call.data.get(ATTR_DATA)
        if (path := call.data.get(ATTR_PATH)) is not None:
            if not hass.config.is_allowed_path(path):
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="path_not_allowed",
                    translation_placeholders={"path": path},
                )
            try:
                data = await hass.async_add_executor_job(_read_text, path)
            except OSError as err:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="path_not_readable",
                    translation_placeholders={"path": path},
                ) from err
        if data is None:
            raise ServiceValidationError(translation_domain=DOMAIN, translation_key="data_required")
        n = await resolve(hass, call.data.get(ATTR_ENTRY)).async_import(data)
        return {"imported": n}

    entry = vol.Optional(ATTR_ENTRY)
    hass.services.async_register(
        DOMAIN,
        "classify_event",
        classify,
        vol.Schema({entry: cv.string, vol.Required(ATTR_EVENT): cv.string, vol.Required(ATTR_CATEGORY): cv.string}),
    )
    hass.services.async_register(
        DOMAIN, "confirm_event", confirm, vol.Schema({entry: cv.string, vol.Required(ATTR_EVENT): cv.string})
    )
    for name, handler in (("reject_event", reject), ("skip_event", skip)):
        hass.services.async_register(
            DOMAIN, name, handler, vol.Schema({entry: cv.string, vol.Required(ATTR_EVENT): cv.string})
        )
    hass.services.async_register(
        DOMAIN, "train_model", train, vol.Schema({entry: cv.string}), supports_response=SupportsResponse.OPTIONAL
    )
    hass.services.async_register(
        DOMAIN, "export_events", export, vol.Schema({entry: cv.string}), supports_response=SupportsResponse.ONLY
    )
    hass.services.async_register(
        DOMAIN,
        "import_events",
        import_,
        vol.Schema(
            {
                entry: cv.string,
                vol.Exclusive(ATTR_DATA, "source"): vol.Any(cv.string, dict),
                vol.Exclusive(ATTR_PATH, "source"): cv.string,
            }
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
