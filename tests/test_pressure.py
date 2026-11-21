"""Exercise write validation, payload preservation and lost acknowledgements."""

from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from grundfos_ble import ControlMode, RequestTimeout
from grundfos_ble.genibus import objects
from grundfos_ble.genibus.apdu import Apdu, DataClass
from grundfos_ble.genibus.telegram import Telegram
from grundfos_ble.registry.alpha2go import PA_PER_METRE

from custom_components.grundfos_ble.pressure import (
    PressureWriteUnverified,
    async_set_pressure,
)

MODE = ControlMode.PROPORTIONAL_PRESSURE
TARGET_M = 3.0
TARGET_PA = TARGET_M * PA_PER_METRE
ORIGINAL = bytes.fromhex("0004") + objects.encode_f32(2.5 * PA_PER_METRE) + bytes(range(12))


class PressureWriteTest(IsolatedAsyncioTestCase):
    def setUp(self):
        self.pump = AsyncMock()
        self.pump.read_operation.side_effect = [
            SimpleNamespace(control_mode=MODE, setpoint=2.5 * PA_PER_METRE),
            SimpleNamespace(control_mode=MODE, setpoint=2.5 * PA_PER_METRE),
            SimpleNamespace(control_mode=MODE, setpoint=TARGET_PA),
        ]
        self.pump.read_factory_limits.return_value = SimpleNamespace(
            min=2 * PA_PER_METRE, max=7 * PA_PER_METRE
        )
        self.pump.get_object.return_value = SimpleNamespace(version=1, data=ORIGINAL)
        self.pump.read_user_setpoint.return_value = SimpleNamespace(setpoint=TARGET_PA)
        reply_apdu = Apdu(DataClass.DATA_OBJECTS, 0, b"\x00")
        self.pump.transport.request.return_value = Telegram.request(reply_apdu)

    async def test_writes_only_the_proportional_pressure_setpoint(self):
        self.assertEqual(await async_set_pressure(self.pump, MODE, TARGET_M), TARGET_M)
        self.pump.transport.request.assert_awaited_once()
        request = self.pump.transport.request.await_args.args[0].apdu
        self.assertEqual(request.data[:3], b"\x56\x00\x12")
        self.assertEqual(
            request.data[-18:],
            ORIGINAL[:2] + objects.encode_f32(TARGET_PA) + ORIGINAL[6:],
        )

    async def test_refuses_out_of_range_value(self):
        with self.assertRaises(ValueError):
            await async_set_pressure(self.pump, MODE, 0.1)
        self.pump.transport.request.assert_not_awaited()

    async def test_refuses_changed_mode(self):
        self.pump.read_operation.side_effect = None
        self.pump.read_operation.return_value = SimpleNamespace(
            control_mode=ControlMode.CONSTANT_PRESSURE
        )
        with self.assertRaises(ValueError):
            await async_set_pressure(self.pump, MODE, TARGET_M)
        self.pump.transport.request.assert_not_awaited()

    async def test_lost_acknowledgement_is_verified_without_resending(self):
        self.pump.transport.request.side_effect = RequestTimeout("ACK missing")
        self.assertEqual(await async_set_pressure(self.pump, MODE, TARGET_M), TARGET_M)
        self.pump.transport.request.assert_awaited_once()
        self.pump.transport.connect.assert_awaited_once()

    async def test_a_different_readback_is_reported_as_unverified(self):
        self.pump.read_user_setpoint.return_value = SimpleNamespace(
            setpoint=2.5 * PA_PER_METRE
        )
        with self.assertRaises(PressureWriteUnverified):
            await async_set_pressure(self.pump, MODE, TARGET_M)
        self.pump.transport.request.assert_awaited_once()
