"""Direct operating commands for the circulation pump."""

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription

from .const import CAP_OPERATION
from .controls import COMMANDS, async_run_command
from .entity import GrundfosEntity


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    if coordinator.has(CAP_OPERATION):
        async_add_entities(PumpCommand(coordinator, key) for key in COMMANDS)


class PumpCommand(GrundfosEntity, ButtonEntity):
    """Send a single operating command when pressed."""

    def __init__(self, coordinator, key: str) -> None:
        super().__init__(coordinator, ButtonEntityDescription(key=key, translation_key=key))

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.controls_enabled

    async def async_press(self) -> None:
        await self.coordinator.async_control(
            lambda pump: async_run_command(pump, self.entity_description.key)
        )
