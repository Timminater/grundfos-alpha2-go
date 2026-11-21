"""Select the pump's control mode or one of its built-in curves."""

import math

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.exceptions import HomeAssistantError

from .const import CAP_OPERATION
from .controls import MODES, PRESETS, async_set_mode, async_set_preset
from .entity import GrundfosEntity
from .pressure import SETPOINT_FACTORS, SETPOINT_STEPS


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    if coordinator.has(CAP_OPERATION):
        async_add_entities([PumpMode(coordinator)])


class PumpMode(GrundfosEntity, SelectEntity):
    """A mode selection also offers the factory I–III presets."""

    _attr_options = [*sorted(MODES), *PRESETS]

    def __init__(self, coordinator) -> None:
        super().__init__(
            coordinator, SelectEntityDescription(key="pump_mode", translation_key="pump_mode")
        )

    @property
    def available(self) -> bool:
        return (
            super().available and self.coordinator.controls_enabled
            and self.coordinator.data.operation is not None
        )

    @property
    def current_option(self) -> str | None:
        operation = self.coordinator.data.operation
        if operation is None:
            return None
        if self.coordinator.last_preset is not None:
            option, mode, value = self.coordinator.last_preset
            if (
                operation.control_mode == mode and operation.setpoint is not None
                and math.isclose(
                    operation.setpoint, value, rel_tol=0,
                    abs_tol=SETPOINT_FACTORS[mode] * SETPOINT_STEPS[mode] / 5,
                )
            ):
                return option
        return next((key for key, mode in MODES.items() if mode == operation.control_mode), None)

    async def async_select_option(self, option: str) -> None:
        if option in PRESETS:
            verified = await self.coordinator.async_control(
                lambda pump: async_set_preset(pump, option)
            )
            self.coordinator.last_preset = (option, PRESETS[option][0], verified)
        elif option in MODES:
            await self.coordinator.async_control(lambda pump: async_set_mode(pump, MODES[option]))
            self.coordinator.last_preset = None
        else:
            raise HomeAssistantError("Unsupported Grundfos control mode")
