"""AutoNight and minimum/maximum flow limiter enable switches."""

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription

from .const import CAP_AUTONIGHT, CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER
from .controls import async_set_autonight, async_set_limiter
from .entity import GrundfosEntity

SWITCHES = {
    "autonight_switch": CAP_AUTONIGHT,
    "maximum_flow_limit_switch": CAP_MAX_FLOW_LIMITER,
    "minimum_flow_limit_switch": CAP_MIN_FLOW_LIMITER,
}


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    coordinator = entry.runtime_data
    async_add_entities(
        PumpSwitch(coordinator, key, capability)
        for key, capability in SWITCHES.items()
        if coordinator.has(capability)
    )


class PumpSwitch(GrundfosEntity, SwitchEntity):
    """Expose one verified on/off setting."""

    def __init__(self, coordinator, key: str, capability: str) -> None:
        super().__init__(
            coordinator, SwitchEntityDescription(key=key, translation_key=key)
        )
        self._capability = capability

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.controls_enabled and self.is_on is not None

    @property
    def is_on(self) -> bool | None:
        data = self.coordinator.data
        if self._capability == CAP_AUTONIGHT:
            return data.autonight
        limiter = data.max_limiter if self._capability == CAP_MAX_FLOW_LIMITER else data.min_limiter
        return limiter.enabled if limiter is not None else None

    async def async_turn_on(self, **kwargs) -> None:
        await self._set(True)

    async def async_turn_off(self, **kwargs) -> None:
        await self._set(False)

    async def _set(self, enabled: bool) -> None:
        if self._capability == CAP_AUTONIGHT:
            await self.coordinator.async_control(lambda pump: async_set_autonight(pump, enabled))
        else:
            await self.coordinator.async_control(
                lambda pump: async_set_limiter(
                    pump, self._capability == CAP_MAX_FLOW_LIMITER, enabled=enabled
                )
            )
