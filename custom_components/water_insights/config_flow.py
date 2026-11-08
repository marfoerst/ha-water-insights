"""Config and options flow."""
from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers import selector

from .const import (
    CONF_BACKFILL,
    CONF_CATEGORIES,
    CONF_CONF_THRESHOLD,
    CONF_END_QUIET_S,
    CONF_HELPER_ENTITIES,
    CONF_MIN_VOLUME_L,
    CONF_SOURCE_ENTITY,
    CONF_SOURCE_MODE,
    CONF_START_FLOW,
    COUNTER_UNITS,
    DEFAULT_CATEGORIES,
    DEFAULT_CONF_THRESHOLD,
    DEFAULT_END_QUIET_S,
    DEFAULT_MIN_VOLUME_L,
    DEFAULT_START_FLOW,
    DOMAIN,
    FLOW_UNITS,
    LABEL_MIXED,
    MODE_COUNTER,
    MODE_FLOW,
)


def detect_mode(unit: str | None) -> str | None:
    """Flow or counter, derived from the unit."""
    if unit in FLOW_UNITS:
        return MODE_FLOW
    if unit in COUNTER_UNITS:
        return MODE_COUNTER
    return None


def _num(min_: float, max_: float, step: float, unit: str | None = None):
    cfg = selector.NumberSelectorConfig(
        min=min_, max=max_, step=step, mode=selector.NumberSelectorMode.BOX
    )
    if unit:
        cfg["unit_of_measurement"] = unit
    return selector.NumberSelector(cfg)


def settings_schema(d: dict[str, Any], include_backfill: bool) -> vol.Schema:
    """Schema for the settings step (shared by config and options flow)."""
    fields: dict[Any, Any] = {
        vol.Required(CONF_CATEGORIES, default=d.get(CONF_CATEGORIES, DEFAULT_CATEGORIES)): selector.TextSelector(
            selector.TextSelectorConfig(multiple=True)
        ),
        vol.Required(CONF_START_FLOW, default=d.get(CONF_START_FLOW, DEFAULT_START_FLOW)): _num(0.1, 20, 0.1, "L/min"),
        vol.Required(CONF_END_QUIET_S, default=d.get(CONF_END_QUIET_S, DEFAULT_END_QUIET_S)): _num(5, 600, 1, "s"),
        vol.Required(CONF_MIN_VOLUME_L, default=d.get(CONF_MIN_VOLUME_L, DEFAULT_MIN_VOLUME_L)): _num(0.1, 100, 0.1, "L"),
        vol.Optional(CONF_HELPER_ENTITIES, default=d.get(CONF_HELPER_ENTITIES, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(
                multiple=True, domain=["sensor", "binary_sensor", "switch", "input_boolean"]
            )
        ),
        vol.Required(
            CONF_CONF_THRESHOLD, default=d.get(CONF_CONF_THRESHOLD, DEFAULT_CONF_THRESHOLD)
        ): _num(0.5, 0.99, 0.01),
    }
    if include_backfill:
        fields[vol.Required(CONF_BACKFILL, default=True)] = selector.BooleanSelector()
    return vol.Schema(fields)


def clean_categories(raw: list[str]) -> list[str]:
    """Strip, de-duplicate, drop the reserved name."""
    out: list[str] = []
    for c in raw:
        c = c.strip()
        if c and c != LABEL_MIXED and c not in out:
            out.append(c)
    return out


class WaterInsightsConfigFlow(ConfigFlow, domain=DOMAIN):
    """Set up a water source."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize."""
        self._data: dict[str, Any] = {}

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick the water sensor."""
        errors: dict[str, str] = {}
        if user_input is not None:
            entity_id = user_input[CONF_SOURCE_ENTITY]
            state = self.hass.states.get(entity_id)
            mode = detect_mode(state.attributes.get("unit_of_measurement") if state else None)
            if mode is None:
                errors[CONF_SOURCE_ENTITY] = "unsupported_unit"
            else:
                await self.async_set_unique_id(entity_id)
                self._abort_if_unique_id_configured()
                self._data = {CONF_SOURCE_ENTITY: entity_id, CONF_SOURCE_MODE: mode}
                return await self.async_step_settings()
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOURCE_ENTITY): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    )
                }
            ),
            errors=errors,
        )

    async def async_step_settings(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Categories, thresholds, helpers."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cats = clean_categories(user_input[CONF_CATEGORIES])
            if not cats:
                errors[CONF_CATEGORIES] = "no_categories"
            else:
                options = {**user_input, CONF_CATEGORIES: cats}
                state = self.hass.states.get(self._data[CONF_SOURCE_ENTITY])
                title = state.name if state else self._data[CONF_SOURCE_ENTITY]
                return self.async_create_entry(title=title, data=self._data, options=options)
        return self.async_show_form(
            step_id="settings",
            data_schema=settings_schema(user_input or {}, include_backfill=True),
            errors=errors,
            description_placeholders={"mode": self._data.get(CONF_SOURCE_MODE, "")},
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Options flow."""
        return WaterInsightsOptionsFlow()


class WaterInsightsOptionsFlow(OptionsFlow):
    """Change settings."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Settings."""
        errors: dict[str, str] = {}
        if user_input is not None:
            cats = clean_categories(user_input[CONF_CATEGORIES])
            if not cats:
                errors[CONF_CATEGORIES] = "no_categories"
            else:
                keep = {CONF_BACKFILL: self.config_entry.options.get(CONF_BACKFILL, True)}
                return self.async_create_entry(data={**keep, **user_input, CONF_CATEGORIES: cats})
        return self.async_show_form(
            step_id="init",
            data_schema=settings_schema({**self.config_entry.options}, include_backfill=False),
            errors=errors,
        )
