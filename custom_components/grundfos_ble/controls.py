"""Verified GENIbus controls for the ALPHA2 GO."""

from __future__ import annotations

import math

from grundfos_ble import GrundfosError, GrundfosPump
from grundfos_ble.genibus import classic, objects
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.pump import encode_control_mode_request
from grundfos_ble.registry import alpha2go as profile

from .pressure import (
    PRESSURE_MODES,
    SETPOINT_FACTORS,
    PressureWriteUnverified,
    async_set_pressure,
    async_verify_pressure,
    async_write_verified,
    pressure_wire,
)

MODES = {mode.name.lower(): mode for mode in profile.MODES_SEEN_ON_ALPHA2_GO}
PRESETS = {
    f"{mode.name.lower()}_{level}": (mode, offset + level - 1)
    for mode, offset in (
        (profile.ControlMode.CONSTANT_CURVE, 0),
        (profile.ControlMode.CONSTANT_PRESSURE, 3),
        (profile.ControlMode.PROPORTIONAL_PRESSURE, 6),
    )
    for level in range(1, 4)
}
COMMANDS = {
    "start": (profile.Command.START, profile.OperationMode.AUTO),
    "stop": (profile.Command.STOP, profile.OperationMode.STOP),
    "min_curve": (profile.Command.MIN, profile.OperationMode.MIN),
    "max_curve": (profile.Command.MAX, profile.OperationMode.MAX),
}
LIMITER_SPECS = {
    True: (profile.MAX_FLOW_LIMITER, profile.MAX_FLOW_LIMITER_RANGE, 1),
    False: (profile.MIN_FLOW_LIMITER, profile.MIN_FLOW_LIMITER_RANGE, 2),
}


async def async_set_mode(pump: GrundfosPump, mode: profile.ControlMode) -> None:
    """Select a recognised mode, keeping that mode's stored setpoint."""
    if mode not in profile.MODES_SEEN_ON_ALPHA2_GO:
        raise ValueError("Unsupported pump mode")
    if (await pump.read_operation()).control_mode == mode:
        return
    await pump.read_factory_limits(mode)

    async def verify() -> None:
        if (await pump.read_operation()).control_mode != mode:
            raise PressureWriteUnverified("The requested control mode was not observed")

    await async_write_verified(
        pump, profile.CONTROL_MODE_REQUEST, 1, encode_control_mode_request(mode), verify
    )


async def async_set_preset(pump: GrundfosPump, option: str) -> float:
    """Apply a pump-stored preset after validating all its data."""
    mode, index = PRESETS[option]
    preset = await pump.get_object(profile.panel_preset(index))
    wire = objects.read_f32(preset.data, 1)
    if preset.version != 1 or len(preset.data) != 5 or preset.data[0] != mode or wire is None:
        raise ValueError("The pump returned an incompatible preset")
    limits = await pump.read_factory_limits(mode)
    if limits.min is None or limits.max is None:
        raise ValueError("The pump did not return the preset limits")
    value = wire / SETPOINT_FACTORS[mode]
    pressure_wire(mode, value, limits.min, limits.max)
    stored = await pump.get_object(profile.USER_SETPOINTS[mode])
    if stored.version != 1 or len(stored.data) != 18:
        raise ValueError("The pump returned an incompatible setpoint object")

    previous = (await pump.read_operation()).control_mode
    await async_set_mode(pump, mode)
    try:
        return (await async_set_pressure(pump, mode, value)) * SETPOINT_FACTORS[mode]
    except (GrundfosError, PressureWriteUnverified, ValueError) as err:
        if previous != mode:
            raise PressureWriteUnverified(
                "The mode changed but the preset could not be verified. Check the pump."
            ) from err
        raise


async def async_read_limiter_range(pump: GrundfosPump, maximum: bool) -> tuple[float, float]:
    """Read the limits specific to this pump and limiter."""
    _, spec, ident = LIMITER_SPECS[maximum]
    reply = await pump.get_object(spec)
    low = objects.read_f32(reply.data, 1)
    high = objects.read_f32(reply.data, 5)
    if (
        reply.version != 1 or len(reply.data) != 9 or reply.data[0] != ident
        or low is None or high is None
        or not (math.isfinite(low) and math.isfinite(high) and 0 <= low <= high)
    ):
        raise ValueError("The pump did not return a valid limiter range")
    return low * profile.SECONDS_PER_HOUR, high * profile.SECONDS_PER_HOUR


async def async_set_limiter(
    pump: GrundfosPump, maximum: bool, *, enabled: bool | None = None,
    value: float | None = None,
) -> None:
    """Change just the requested limiter field and preserve controller parameters."""
    spec, _, ident = LIMITER_SPECS[maximum]
    reply = await pump.get_object(spec)
    data = reply.data
    if reply.version != 1 or len(data) != 18 or data[0] != ident or data[1] not in (0, 1):
        raise ValueError("The pump returned an incompatible limiter object")
    use_limit = bool(data[1]) if enabled is None else enabled
    wire = objects.read_f32(data, 2) if value is None else value / profile.SECONDS_PER_HOUR
    if value is not None or use_limit:
        low, high = await async_read_limiter_range(pump, maximum)
        if (
            wire is None or not math.isfinite(wire)
            or not low <= wire * profile.SECONDS_PER_HOUR <= high
            or (use_limit and maximum and wire <= 0)
        ):
            raise ValueError("The flow limit is outside this pump's range")
    if use_limit:
        other = await pump.get_object(LIMITER_SPECS[not maximum][0])
        other_limit = objects.read_f32(other.data, 2)
        if other.version != 1 or len(other.data) != 18 or other.data[1] not in (0, 1):
            raise ValueError("The other flow limit could not be checked")
        if other.data[1] and (
            wire is None or other_limit is None or not math.isfinite(other_limit)
            or (wire < other_limit if maximum else wire > other_limit)
        ):
            raise ValueError("The minimum limit must not exceed the maximum limit")
    payload = data[:1] + bytes((use_limit,)) + (
        data[2:6] if value is None else objects.encode_f32(wire)
    ) + data[6:]
    if payload == data:
        return

    async def verify() -> None:
        actual = (await pump.get_object(spec)).data
        if actual[:6] != payload[:6]:
            raise PressureWriteUnverified("The requested flow limit was not observed")

    await async_write_verified(pump, spec, 1, payload, verify)


async def async_set_autonight(pump: GrundfosPump, enabled: bool) -> None:
    """Switch night setback on or off and confirm the reported setting."""
    if await pump.read_autonight() == enabled:
        return

    async def verify() -> None:
        if await pump.read_autonight() != enabled:
            raise PressureWriteUnverified("The requested AutoNight setting was not observed")

    await async_write_verified(pump, profile.AUTONIGHT, 1, bytes((enabled,)), verify)


async def async_run_command(pump: GrundfosPump, key: str) -> None:
    """Run one operating command and check the realised operation."""
    command, expected = COMMANDS[key]
    try:
        reply = await pump.transport.request(Telegram.request(classic.command(command)))
        classic.parse_command_reply(reply.apdu, reply.raw)
    except GrundfosError:
        await pump.transport.disconnect()
        await pump.transport.connect()
    if (await pump.read_operation()).operation_mode != expected:
        raise PressureWriteUnverified("The requested operating state was not observed")


def proportional_setpoint(
    flow: float, head: float, minimum: float, maximum: float, raw: bytes
) -> float:
    """Solve for Hset using the pump's reported straight-line PP curve."""
    if len(raw) != 25 or raw[0] != 0:
        raise ValueError("This proportional pressure curve is unsupported")
    foot = objects.read_f32(raw, 21)
    a, b, c = (objects.read_f32(raw, offset) for offset in (9, 13, 17))
    if (
        None in (foot, a, b, c)
        or not all(math.isfinite(v) for v in (foot, a, b, c, minimum, maximum, flow, head))
        or not 0 < foot < 1 or not 0 < minimum <= maximum
    ):
        raise ValueError("The pump did not return usable curve coefficients")

    def head_at(setpoint: float) -> float:
        endpoint = a + b * setpoint + c * setpoint**2
        if endpoint <= 0 or not math.isfinite(endpoint):
            raise ValueError("The pump reported an invalid curve endpoint")
        base = max(foot * setpoint, profile.PA_PER_METRE)
        return base + (setpoint - base) * (flow / profile.SECONDS_PER_HOUR) / endpoint

    target = head * profile.PA_PER_METRE
    if not head_at(minimum) <= target <= head_at(maximum):
        raise ValueError("The duty point is outside the pump's proportional pressure range")
    lo, hi = minimum, maximum
    for _ in range(40):
        mid = (lo + hi) / 2
        if head_at(mid) < target:
            lo = mid
        else:
            hi = mid
    setpoint = round((lo + hi) / (2 * profile.PA_PER_METRE), 1)
    endpoint = a + b * setpoint * profile.PA_PER_METRE + c * (setpoint * profile.PA_PER_METRE)**2
    if flow / profile.SECONDS_PER_HOUR > endpoint:
        raise ValueError("The target flow is beyond this pressure curve")
    return min(max(setpoint, minimum / profile.PA_PER_METRE), maximum / profile.PA_PER_METRE)


async def async_set_duty_point(pump: GrundfosPump, flow: float, head: float) -> None:
    """Apply a duty point in the active constant or proportional pressure mode."""
    flow, head = float(flow), float(head)
    if not math.isfinite(flow) or not math.isfinite(head) or flow < 0 or head <= 0:
        raise ValueError("Enter a non-negative flow and a positive head")
    mode = (await pump.read_operation()).control_mode
    if mode not in PRESSURE_MODES:
        raise ValueError("Select constant or proportional pressure first")
    if mode == profile.ControlMode.PROPORTIONAL_PRESSURE:
        limits = await pump.read_factory_limits(mode)
        if limits.min is None or limits.max is None:
            raise ValueError("The pump did not return the pressure limits")
        curve = await pump.get_object(profile.PP_CURVE)
        if curve.version != 1:
            raise ValueError("This pressure curve version is unsupported")
        head = proportional_setpoint(flow, head, limits.min, limits.max, curve.data)
    await async_set_pressure(pump, mode, head)
