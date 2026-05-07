"""Birdfy sensor — last detection event."""
from __future__ import annotations

import time

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import BirdfyCoordinator


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: BirdfyCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([BirdfyLastEventSensor(coordinator, entry)])


class BirdfyLastEventSensor(CoordinatorEntity[BirdfyCoordinator], SensorEntity):
    """Last detection event from the Birdfy camera."""

    _attr_icon = "mdi:bird"

    def __init__(self, coordinator: BirdfyCoordinator, entry: ConfigEntry) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_last_event"
        self._attr_name = "Birdfy Last Event"

    @property
    def native_value(self) -> str:
        last = self.coordinator.data.get("last_event", {})
        return last.get("label", "unknown")

    @property
    def extra_state_attributes(self) -> dict:
        data = self.coordinator.data
        last = data.get("last_event", {})
        alert_time = last.get("alert_time", 0)
        return {
            "alarm_id":      last.get("alarm_id"),
            "alert_time":    alert_time,
            "alert_time_iso": time.strftime(
                "%Y-%m-%dT%H:%M:%S", time.localtime(alert_time / 1000)
            ) if alert_time else None,
            "record_url":    last.get("record_url"),
            "image_url":     self.coordinator.image_url,
            "highlights_url": self.coordinator.highlights_url,
            "device_id":     data.get("device_id"),
            "recent_events": data.get("recent_events", []),
        }
