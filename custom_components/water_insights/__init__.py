"""Water Insights: learn what your water is used for."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import DOMAIN
from .manager import WaterInsightsManager
from .panel import async_register_panel, async_unregister_panel
from .services import async_setup_services
from .websocket_api import async_setup_websocket

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type WaterInsightsConfigEntry = ConfigEntry[WaterInsightsManager]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up services and websocket commands."""
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WaterInsightsConfigEntry) -> bool:
    """Set up one water source."""
    manager = WaterInsightsManager(hass, entry)
    await manager.async_start()
    entry.runtime_data = manager
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_register_panel(hass)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def _async_reload(hass: HomeAssistant, entry: WaterInsightsConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WaterInsightsConfigEntry) -> bool:
    """Unload."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_stop()
    # The panel stays while entries reload (saving settings reloads the entry and would
    # otherwise kick open browser tabs off the page); it goes with the last removed entry.
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: WaterInsightsConfigEntry) -> None:
    """Delete stored draws and model when the entry is removed."""
    from homeassistant.helpers.storage import Store  # noqa: PLC0415

    await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}").async_remove()
    if not [e for e in hass.config_entries.async_entries(DOMAIN) if e.entry_id != entry.entry_id]:
        async_unregister_panel(hass)
