"""Birdfy integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import BirdfyCoordinator
from .http import register_views

PLATFORMS = [Platform.SENSOR, Platform.CAMERA]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    coordinator = BirdfyCoordinator(
        hass,
        email=entry.data.get(CONF_EMAIL) or entry.data.get("email", ""),
        password=entry.data.get(CONF_PASSWORD) or entry.data.get("password", ""),
        ucid=entry.data.get("ucid", ""),
        udid=entry.data.get("udid", ""),
    )
    await coordinator.async_config_entry_first_refresh()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    register_views(hass)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unloaded
