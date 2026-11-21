"""Write one verified ALPHA2 GO pressure setpoint over the existing BLE transport."""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable

from grundfos_ble import ControlMode, GrundfosError, GrundfosPump, ProtocolError
from grundfos_ble.genibus import objects
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.registry import alpha2go
from grundfos_ble.registry.base import Access, ObjectSpec

PRESSURE_MODES = (
    ControlMode.CONSTANT_PRESSURE,
    ControlMode.PROPORTIONAL_PRESSURE,
)
PRESSURE_STEP_M = 0.1
SETPOINT_FACTORS = {
    ControlMode.CONSTANT_PRESSURE: alpha2go.PA_PER_METRE,
    ControlMode.PROPORTIONAL_PRESSURE: alpha2go.PA_PER_METRE,
    ControlMode.CONSTANT_CURVE: 1.0,
    ControlMode.CONSTANT_FLOW: 1 / alpha2go.SECONDS_PER_HOUR,
}
SETPOINT_STEPS = {
    ControlMode.CONSTANT_PRESSURE: PRESSURE_STEP_M,
    ControlMode.PROPORTIONAL_PRESSURE: PRESSURE_STEP_M,
    ControlMode.CONSTANT_CURVE: 1.0,
    ControlMode.CONSTANT_FLOW: 0.01,
}


class PressureWriteUnverified(Exception):
    """A SET may have reached the pump, but the result could not be verified."""


def pressure_wire(mode: ControlMode, metres: float, lower: float, upper: float) -> float:
    """Convert a valid request to Pa, using the connected pump's limits."""
    if mode not in SETPOINT_FACTORS or not all(
        math.isfinite(value) for value in (metres, lower, upper)
    ):
        raise ValueError("A supported control mode and finite limits are required")
    wire = alpha2go.setpoint_wire(mode, metres)
    if wire is None or metres < 0 or not lower <= wire <= upper:
        raise ValueError("The requested setpoint is outside this pump's range")
    return wire


async def async_set_pressure(
    pump: GrundfosPump, mode: ControlMode, metres: float
) -> float:
    """Change the stored value for the active mode and verify it without retransmitting SET."""
    if mode not in SETPOINT_FACTORS or (await pump.read_operation()).control_mode != mode:
        raise ValueError("The pump changed mode; wait for a fresh reading")

    limits = await pump.read_factory_limits(mode)
    if limits.min is None or limits.max is None:
        raise ValueError("The pump did not return its setpoint limits")
    requested = pressure_wire(mode, metres, limits.min, limits.max)
    spec = alpha2go.USER_SETPOINTS[mode]
    current = await pump.get_object(spec)
    if spec.type != 302 or current.version != 1 or len(current.data) != 18:
        raise ValueError("The setpoint configuration format is unsupported")

    payload = current.data[:2] + objects.encode_f32(requested) + current.data[6:]
    if payload == current.data:
        return await async_verify_pressure(pump, mode, requested)
    if (await pump.read_operation()).control_mode != mode:
        raise ValueError("The pump changed mode before the write")

    return await async_write_verified(
        pump, spec, current.version, payload,
        lambda: async_verify_pressure(pump, mode, requested),
    )


async def async_write_verified[T](
    pump: GrundfosPump, spec: ObjectSpec, version: int, payload: bytes,
    verify: Callable[[], Awaitable[T]],
) -> T:
    """Send one SET and run the caller's read-back, without repeating a write."""
    if spec.access is not Access.READ_WRITE or spec.type is None or spec.version != version:
        raise ValueError("Unsupported write object type or version")
    request = objects.set_request(spec.ref, spec.type, version, payload)
    try:
        reply = await pump.transport.request(Telegram.request(request))
        status = objects.parse_set_reply(reply.apdu, reply.raw)
        if status != objects.ObjectStatus.OK:
            raise ProtocolError(f"The pump returned SET status {status}")
    except GrundfosError:
        # The application-level acknowledgement can be lost after the pump applies SET.
        try:
            await pump.transport.disconnect()
            await pump.transport.connect()
            return await verify()
        except (GrundfosError, PressureWriteUnverified) as read_error:
            raise PressureWriteUnverified(
                "The write was not confirmed; check the current setting before retrying"
            ) from read_error
    return await verify()


async def async_verify_pressure(pump: GrundfosPump, mode: ControlMode, requested: float) -> float:
    """Verify both the stored setpoint and the setting realised by the pump."""
    try:
        stored = await pump.read_user_setpoint(mode)
        operation = await pump.read_operation()
    except GrundfosError as err:
        raise PressureWriteUnverified("The pump did not return a fresh setting") from err

    factor = SETPOINT_FACTORS[mode]
    tolerance = SETPOINT_STEPS[mode] * factor / 5
    if (
        operation.control_mode != mode
        or stored.setpoint is None
        or operation.setpoint is None
        or not math.isclose(stored.setpoint, requested, rel_tol=0, abs_tol=tolerance)
        or not math.isclose(operation.setpoint, requested, rel_tol=0, abs_tol=tolerance)
    ):
        raise PressureWriteUnverified(
            "The pump reported a different mode or setpoint after the write"
        )
    return operation.setpoint / factor
