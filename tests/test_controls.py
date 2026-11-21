"""Verify control frames and failure behavior without a physical pump."""

import struct
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from grundfos_ble import RequestTimeout
from grundfos_ble.genibus import objects
from grundfos_ble.genibus.apdu import Apdu, DataClass
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.registry import alpha2go as profile

from custom_components.grundfos_ble.controls import (
    async_read_limiter_range,
    async_run_command,
    async_set_autonight,
    async_set_limiter,
    async_set_mode,
    async_set_preset,
    proportional_setpoint,
)
from custom_components.grundfos_ble.pressure import PressureWriteUnverified, pressure_wire


def ack(data_class, data=b"\x00"):
    return Telegram.request(Apdu(data_class, 0, data))


def object_reply(type_id, data):
    return objects.ObjectReply(objects.ObjectHeader(0, type_id, 1, len(data)), data)


class ControlTests(IsolatedAsyncioTestCase):
    def setUp(self):
        self.pump = AsyncMock()
        self.pump.transport.request.return_value = ack(DataClass.DATA_OBJECTS)

    async def test_reads_limiter_range_from_real_reply(self):
        frame = bytes.fromhex(
            "24 14 f8 e7 0a 10 00 03 81 01 00 00 09 01 39 da 74 0e 3a 7e dc bb 44 1a"
        )
        self.pump.get_object.return_value = objects.parse_get_reply(
            Telegram.decode(frame).apdu
        )[0]
        low, high = await async_read_limiter_range(self.pump, True)
        self.assertAlmostEqual(low, 1.5, places=2)
        self.assertAlmostEqual(high, 3.5, places=2)

    async def test_mode_command_keeps_the_stored_setpoint(self):
        self.pump.read_operation.side_effect = [
            SimpleNamespace(control_mode=profile.ControlMode.PROPORTIONAL_PRESSURE),
            SimpleNamespace(control_mode=profile.ControlMode.CP_AUTOADAPT),
        ]
        await async_set_mode(self.pump, profile.ControlMode.CP_AUTOADAPT)
        request = self.pump.transport.request.await_args.args[0].apdu
        self.assertEqual(request.data[:3], b"\x56\x00\x0a")
        self.assertEqual(request.data[-7:], b"\x00\x06\x0e\x7f\xff\xff\xff")

    async def test_wrong_preset_aborts_before_mode_switch(self):
        self.pump.get_object.return_value = object_reply(869, b"\x02" + struct.pack(">f", 10000))
        with self.assertRaises(ValueError):
            await async_set_preset(self.pump, "constant_pressure_1")
        self.pump.transport.request.assert_not_awaited()

    async def test_preset_uses_the_value_stored_by_the_pump(self):
        mode = profile.ControlMode.CONSTANT_PRESSURE
        wire = 3 * profile.PA_PER_METRE
        previous = SimpleNamespace(control_mode=profile.ControlMode.PROPORTIONAL_PRESSURE)
        active = SimpleNamespace(control_mode=mode, setpoint=wire)
        self.pump.read_operation.side_effect = [previous, previous, active, active, active, active]
        self.pump.read_factory_limits.return_value = SimpleNamespace(min=10000, max=60000)
        config = object_reply(302, bytes(18))
        self.pump.get_object.side_effect = [
            object_reply(869, bytes((mode,)) + struct.pack(">f", wire)),
            config,
            config,
        ]
        self.pump.read_user_setpoint.return_value = SimpleNamespace(setpoint=wire)

        self.assertEqual(await async_set_preset(self.pump, "constant_pressure_1"), wire)
        requests = [call.args[0].apdu for call in self.pump.transport.request.await_args_list]
        self.assertEqual([req.data[:3] for req in requests], [b"\x56\x00\x0a", b"\x56\x00\x10"])

    async def test_autonight_writes_one_boolean(self):
        self.pump.read_autonight.side_effect = [False, True]
        await async_set_autonight(self.pump, True)
        request = self.pump.transport.request.await_args.args[0].apdu
        self.assertEqual(request.data[:3], b"\x5a\x00\x08")
        self.assertEqual(request.data[-1:], b"\x01")

    async def test_limiter_edits_only_the_value(self):
        original = b"\x01\x00" + struct.pack(">f", 0.0005) + b"\xaa" * 12
        expected = original[:2] + struct.pack(">f", 2 / 3600) + original[6:]
        self.pump.get_object.side_effect = [
            object_reply(895, original),
            object_reply(897, b"\x01" + struct.pack(">ff", 0.0001, 0.001)),
            object_reply(895, expected),
        ]
        await async_set_limiter(self.pump, True, value=2)
        request = self.pump.transport.request.await_args.args[0].apdu
        self.assertEqual(request.data[:3], b"\x56\x02\x58")
        self.assertEqual(request.data[-18:], expected)

    async def test_limiter_cannot_overtake_the_other_enabled_limit(self):
        self.pump.get_object.side_effect = [
            object_reply(895, b"\x01\x01" + struct.pack(">f", 0.0005) + bytes(12)),
            object_reply(897, b"\x01" + struct.pack(">ff", 0.0001, 0.001)),
            object_reply(895, b"\x02\x01" + struct.pack(">f", 0.0007) + bytes(12)),
        ]
        with self.assertRaises(ValueError):
            await async_set_limiter(self.pump, True, value=2)
        self.pump.transport.request.assert_not_awaited()

    async def test_stop_with_lost_ack_is_checked_without_resend(self):
        self.pump.transport.request.side_effect = RequestTimeout("ACK missing")
        self.pump.read_operation.return_value = SimpleNamespace(
            operation_mode=profile.OperationMode.STOP
        )
        await async_run_command(self.pump, "stop")
        self.pump.transport.request.assert_awaited_once()
        self.assertEqual(self.pump.transport.request.await_args.args[0].apdu.data, b"\x05")

    async def test_wrong_operating_state_is_unverified(self):
        self.pump.transport.request.return_value = ack(DataClass.COMMANDS, b"")
        self.pump.read_operation.return_value = SimpleNamespace(
            operation_mode=profile.OperationMode.AUTO
        )
        with self.assertRaises(PressureWriteUnverified):
            await async_run_command(self.pump, "max_curve")
        self.pump.transport.request.assert_awaited_once()


class SetpointTests(TestCase):
    def test_constant_flow_uses_cubic_metres_per_second_on_the_wire(self):
        self.assertAlmostEqual(
            pressure_wire(profile.ControlMode.CONSTANT_FLOW, 1.8, 0.0001, 0.001),
            0.0005,
        )

    def test_proportional_duty_point_uses_the_reported_curve(self):
        raw = b"\x00" + struct.pack(">6f", 0.5, 0, 0.001, 0, 0, 0.5)
        self.assertEqual(
            proportional_setpoint(1.8, 3, 2 * profile.PA_PER_METRE, 7 * profile.PA_PER_METRE, raw),
            4,
        )
