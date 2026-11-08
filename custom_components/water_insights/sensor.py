"""Per-category water sensors."""
from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util
from homeassistant.util import slugify

from . import WaterInsightsConfigEntry
from .const import DOMAIN, LABEL_MIXED, LABEL_SMALL, LABEL_UNCLASSIFIED
from .manager import WaterInsightsManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WaterInsightsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create sensors."""
    m = entry.runtime_data
    entities: list[SensorEntity] = [CategorySensor(m, c, None) for c in m.categories]
    entities += [CategorySensor(m, k, k) for k in (LABEL_MIXED, LABEL_UNCLASSIFIED, LABEL_SMALL)]
    entities += [ModelStatusSensor(m), LastTrainedSensor(m)]
    wanted = {e.unique_id for e in entities}
    reg = er.async_get(hass)
    for reg_entry in er.async_entries_for_config_entry(reg, entry.entry_id):
        if reg_entry.domain == "sensor" and reg_entry.unique_id not in wanted:
            reg.async_remove(reg_entry.entity_id)  # category was removed
    async_add_entities(entities)


class _Base(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, manager: WaterInsightsManager) -> None:
        self.manager = manager
        entry = manager.entry
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name=f"Water Insights ({entry.title})",
            manufacturer="Water Insights",
            model="Water draw classifier",
        )

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(self.manager.add_listener(self._update))

    @callback
    def _update(self) -> None:
        self.async_write_ha_state()


class CategorySensor(_Base):
    """Liters attributed to one category (may decrease after corrections)."""

    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = UnitOfVolume.LITERS
    _attr_suggested_display_precision = 1

    def __init__(self, manager: WaterInsightsManager, key: str, translation_key: str | None) -> None:
        super().__init__(manager)
        self.key = key
        self._attr_unique_id = f"{manager.entry.entry_id}_{slugify(key)}"
        if translation_key:
            self._attr_translation_key = translation_key
        else:
            self._attr_name = key

    @property
    def native_value(self) -> float:
        return self.manager.totals().get(self.key, 0.0)


class ModelStatusSensor(_Base):
    """untrained / trained / stale, with per-class metrics."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "model_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["untrained", "trained", "stale"]

    def __init__(self, manager: WaterInsightsManager) -> None:
        super().__init__(manager)
        self._attr_unique_id = f"{manager.entry.entry_id}_model_status"

    @property
    def native_value(self) -> str:
        return self.manager.model_status()

    @property
    def extra_state_attributes(self) -> dict:
        res = self.manager.train_result()
        if res is None:
            return {}
        return {
            "model": res.chosen,
            "auto_classes": sorted(res.enabled_classes()),
            "per_class": {
                c: {
                    "n": m.n_total,
                    "auto_precision": m.auto_precision,
                    "auto_coverage": m.auto_coverage,
                    "enabled": m.enabled,
                }
                for c, m in res.per_class.items()
            },
        }


class LastTrainedSensor(_Base):
    """Timestamp of the last training run."""

    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_translation_key = "last_trained"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, manager: WaterInsightsManager) -> None:
        super().__init__(manager)
        self._attr_unique_id = f"{manager.entry.entry_id}_last_trained"

    @property
    def native_value(self):
        res = self.manager.train_result()
        return dt_util.utc_from_timestamp(res.trained_at) if res else None
