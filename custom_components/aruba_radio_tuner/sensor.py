"""Status entity for the Aruba Radio Tuner panel/card."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from .const import DOMAIN
from .manager import ArubaRunManager


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add a single status sensor for the cluster manager."""
    manager: ArubaRunManager = hass.data[DOMAIN][entry.entry_id]
    entity = ArubaRadioTunerSensor(manager, entry)
    async_add_entities([entity])


class ArubaRadioTunerSensor(SensorEntity):
    """Expose current run, AP inventory, schedules, and saved results."""

    _attr_has_entity_name = True
    _attr_name = "Radio tuner"
    _attr_icon = "mdi:access-point-network"

    def __init__(self, manager: ArubaRunManager, entry: ConfigEntry) -> None:
        self.manager = manager
        self._attr_unique_id = f"{entry.entry_id}_radio_tuner"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "manufacturer": "HPE Aruba Networking",
            "model": "Instant REST API",
            "name": entry.title,
        }

    async def async_added_to_hass(self) -> None:
        """Subscribe to the manager once the entity is registered."""
        await super().async_added_to_hass()
        self.manager.set_sensor_update(self.async_write_ha_state)

    @property
    def native_value(self) -> str:
        return self.manager.data.get("status", "idle")

    @property
    def extra_state_attributes(self) -> dict:
        return {
            "entry_id": self.manager.entry_id,
            "api_version": self.manager.data.get("api_version"),
            "aps": self.manager.data.get("aps", []),
            "active_run": self.manager.data.get("active_run"),
            "results": self.manager.data.get("results", []),
            "schedules": self.manager.data.get("schedules", []),
            "last_error": self.manager.data.get("last_error"),
        }
