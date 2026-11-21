"""Optional setpoint controls for ALPHA2 GO pressure modes."""

from __future__ import annotations

from grundfos_ble import ControlMode
from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.const import UnitOfLength
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import CAP_MAX_FLOW_LIMITER, CAP_MIN_FLOW_LIMITER, CAP_OPERATION
from .controls import async_set_limiter
from .coordinator import GrundfosConfigEntry, GrundfosCoordinator
from .entity import GrundfosEntity
from .pressure import SETPOINT_FACTORS, SETPOINT_STEPS

FLOW_UNIT = "m³/h"


async def async_setup_entry(
    hass: HomeAssistant, entry: GrundfosConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Register each pressure mode as a separate control with a fixed meaning."""
    coordinator = entry.runtime_data
    if coordinator.has(CAP_OPERATION):
        async_add_entities(
            PumpSetpoint(coordinator, mode, key, unit)
            for mode, key, unit in (
                (ControlMode.CONSTANT_PRESSURE, "constant_pressure_setpoint", UnitOfLength.METERS),
                (ControlMode.PROPORTIONAL_PRESSURE, "proportional_pressure_setpoint", UnitOfLength.METERS),
                (ControlMode.CONSTANT_CURVE, "speed_setpoint", "rpm"),
                (ControlMode.CONSTANT_FLOW, "flow_setpoint", FLOW_UNIT),
            )
        )
    async_add_entities(
        FlowLimit(coordinator, maximum)
        for maximum, cap in ((True, CAP_MAX_FLOW_LIMITER), (False, CAP_MIN_FLOW_LIMITER))
        if coordinator.has(cap)
    )


class PumpSetpoint(GrundfosEntity, NumberEntity):
    """Change a mode's setpoint only while it is active."""

    _attr_mode = NumberMode.BOX

    def __init__(
        self, coordinator: GrundfosCoordinator, mode: ControlMode, key: str, unit: str
    ) -> None:
        super().__init__(
            coordinator, NumberEntityDescription(key=key, translation_key=key)
        )
        self._control_mode = mode
        self._factor = SETPOINT_FACTORS[mode]
        self._attr_native_step = SETPOINT_STEPS[mode]
        self._attr_native_unit_of_measurement = unit

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.coordinator.controls_enabled
            and self.coordinator.data.setpoint_limits is not None
            and self.native_value is not None
        )

    @property
    def native_value(self) -> float | None:
        operation = self.coordinator.data.operation
        if (
            operation is None
            or operation.control_mode != self._control_mode
            or operation.setpoint is None
        ):
            return None
        return round(operation.setpoint / self._factor, 3)

    @property
    def native_min_value(self) -> float:
        limits = self.coordinator.data.setpoint_limits
        return limits[0] / self._factor if limits else 0

    @property
    def native_max_value(self) -> float:
        limits = self.coordinator.data.setpoint_limits
        return limits[1] / self._factor if limits else 0

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_set_setpoint(self._control_mode, value)


class FlowLimit(GrundfosEntity, NumberEntity):
    """Change a limiter value independently of enabling it."""

    _attr_mode = NumberMode.BOX
    _attr_native_unit_of_measurement = FLOW_UNIT
    _attr_native_step = 0.01

    def __init__(self, coordinator: GrundfosCoordinator, maximum: bool) -> None:
        key = "maximum_flow_limit" if maximum else "minimum_flow_limit"
        super().__init__(coordinator, NumberEntityDescription(key=key, translation_key=key))
        self._maximum = maximum

    @property
    def _range(self) -> tuple[float, float] | None:
        data = self.coordinator.data
        return data.max_flow_range if self._maximum else data.min_flow_range

    @property
    def available(self) -> bool:
        return (
            super().available and self.coordinator.controls_enabled
            and self._range is not None and self.native_value is not None
        )

    @property
    def native_value(self) -> float | None:
        data = self.coordinator.data
        limiter = data.max_limiter if self._maximum else data.min_limiter
        return limiter.limit_m3h if limiter is not None else None

    @property
    def native_min_value(self) -> float:
        return self._range[0] if self._range is not None else 0

    @property
    def native_max_value(self) -> float:
        return self._range[1] if self._range is not None else 0

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_control(
            lambda pump: async_set_limiter(pump, self._maximum, value=value)
        )
