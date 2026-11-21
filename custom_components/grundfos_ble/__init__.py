"""Grundfos Bluetooth: Grundfos ALPHA2 GO circulator pumps over GENIbus-on-BLE."""

from __future__ import annotations

import logging
import math

import probatio as vol

from bleak_retry_connector import close_stale_connections_by_address
from homeassistant.components import bluetooth
from homeassistant.const import CONF_ADDRESS, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .controls import async_set_duty_point
from .coordinator import GrundfosConfigEntry, GrundfosCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR, Platform.SENSOR, Platform.NUMBER,
    Platform.SELECT, Platform.SWITCH, Platform.BUTTON,
]


def _finite_number(value: object) -> float:
    number = vol.Coerce(float)(value)
    if not math.isfinite(number):
        raise vol.Invalid("Expected a finite number")
    return number


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register the duty-point action for the selected pump."""

    async def set_duty_point(call: ServiceCall) -> None:
        entry = hass.config_entries.async_get_entry(call.data["config_entry_id"])
        coordinator = getattr(entry, "runtime_data", None)
        if entry is None or entry.domain != DOMAIN or not isinstance(coordinator, GrundfosCoordinator):
            raise HomeAssistantError("Select a loaded Grundfos pump")
        await coordinator.async_control(
            lambda pump: async_set_duty_point(pump, call.data["flow"], call.data["head"])
        )

    hass.services.async_register(
        DOMAIN, "set_duty_point", set_duty_point,
        schema=vol.Schema({
            vol.Required("config_entry_id"): str,
            vol.Required("flow"): vol.All(_finite_number, vol.Range(min=0)),
            vol.Required("head"): vol.All(_finite_number, vol.Range(min=0, min_included=False)),
        }),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Set up a pump from a config entry.

    The pump may be powered only while a thermostat calls for it, so setup does not
    require it to be reachable: the coordinator polls when it advertises and the entities
    stay unavailable until then (quality scale: test-before-setup, temporary unavailability).
    """
    # A link left open by an earlier run (a restart mid-poll on a local adapter) would hold the
    # pump's one client slot and fail every poll; yalexs_ble guards against the same.
    await close_stale_connections_by_address(entry.data[CONF_ADDRESS])
    await er.async_migrate_entries(hass, entry.entry_id, _migrate_unique_id)
    coordinator = GrundfosCoordinator(hass, _LOGGER, entry)
    entry.runtime_data = coordinator
    entry.async_on_unload(coordinator.async_start())
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> bool:
    """Unload a config entry; a poll still running is cancelled, so it cannot outlive it."""
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await entry.runtime_data.async_stop_polling()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: GrundfosConfigEntry) -> None:
    """Offer the pump for discovery again once its entry is gone."""
    bluetooth.async_rediscover_address(hass, entry.data[CONF_ADDRESS])


@callback
def _migrate_unique_id(entity: er.RegistryEntry) -> dict[str, str] | None:
    """Rename the "shaft power" sensor to "DC power" (0.2.0), keeping its history."""
    if entity.unique_id.endswith("_shaft_power"):
        return {"new_unique_id": entity.unique_id.removesuffix("_shaft_power") + "_dc_power"}
    return None
