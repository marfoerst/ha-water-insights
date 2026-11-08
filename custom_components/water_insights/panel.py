"""Sidebar panel registration."""
from __future__ import annotations

from pathlib import Path

from homeassistant.components import frontend, panel_custom
from homeassistant.components.http import StaticPathConfig
from homeassistant.core import HomeAssistant, callback

from .const import DOMAIN

PANEL_URL = "water-insights"
STATIC_URL = f"/{DOMAIN}_static"
_KEY_STATIC = f"{DOMAIN}_static_registered"
_KEY_PANEL = f"{DOMAIN}_panel_registered"


async def async_register_panel(hass: HomeAssistant) -> None:
    """Register static files and the sidebar panel once."""
    if not hass.data.get(_KEY_STATIC):
        await hass.http.async_register_static_paths(
            [StaticPathConfig(STATIC_URL, str(Path(__file__).parent / "frontend"), False)]
        )
        hass.data[_KEY_STATIC] = True
    if hass.data.get(_KEY_PANEL):
        return
    from homeassistant.loader import async_get_integration  # noqa: PLC0415

    version = (await async_get_integration(hass, DOMAIN)).version
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=PANEL_URL,
        webcomponent_name="water-insights-panel",
        sidebar_title="Water Insights",
        sidebar_icon="mdi:water-sync",
        module_url=f"{STATIC_URL}/water-insights-panel.js?v={version}",
        require_admin=True,
        config={},
    )
    hass.data[_KEY_PANEL] = True


@callback
def async_unregister_panel(hass: HomeAssistant) -> None:
    """Remove the panel when the last entry goes away."""
    if hass.data.pop(_KEY_PANEL, None):
        frontend.async_remove_panel(hass, PANEL_URL)
