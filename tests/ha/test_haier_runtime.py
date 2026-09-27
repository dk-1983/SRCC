"""Real HA events/services with a simulated MQTT result subscription and ESP."""
import json
import sys
import unittest
from types import SimpleNamespace, ModuleType
from unittest.mock import AsyncMock, Mock, patch

import test_execution as fixtures
from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
from custom_components.fourvrs_server_room.runtime import Runtime
from custom_components.fourvrs_server_room.configuration import validate, ConfigurationError
from custom_components.fourvrs_server_room.config_flow import validate_haier_binding


def packet(**changes):
    data = dict(schema=1, boot_id="a"*32, status_frames=10, age_ms=0,
                sample_uptime_ms=10000, published_uptime_ms=10000,
                available=True, mode="COOL", target_temperature=23,
                current_temperature=24, fault_valid=True, fault=False, fault_code=0,
                request_id="mqtt-1", command_state="confirmed")
    data.update(changes)
    return data


class HaierRuntimeTests(unittest.IsolatedAsyncioTestCase):
    service = fixtures.ExecutionTests.service
    settle = fixtures.ExecutionTests.settle
    names = fixtures.ExecutionTests.names
    asyncTearDown = fixtures.ExecutionTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.ExecutionTests.asyncSetUp(self)
        self.runtime.stop()
        self.data = validate(dict(fixtures.settings(), ac_telemetry="sensor.haier_data",
                                  haier_result_topic="test-haier/result"))
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        self.hass.states.async_set("sensor.haier_data", "10", packet())
        self.runtime = Runtime(self.hass, SimpleNamespace(entry_id="test"), self.data)
        self.now = self.runtime._clock_anchor
        for name in ("runtime", "execution"):
            clock = patch(f"custom_components.fourvrs_server_room.{name}.monotonic", side_effect=lambda: self.now)
            clock.start(); self.addCleanup(clock.stop)
        self.runtime.start()
        self.runtime.haier_result_ready = True
        self.feedback = False

    async def publish(self, elapsed=0, **changes):
        self.now = self.runtime._clock_anchor + elapsed
        self.hass.states.async_set("sensor.haier_data", str(changes.get("status_frames", 10)),
                                   packet(**changes), force_update=True)
        await self.settle()

    async def result(self, **payload):
        self.runtime._haier_result(SimpleNamespace(payload=json.dumps(payload), retain=False))
        await self.settle()

    async def test_startup_waits_for_live_event_then_reads_one_celsius_snapshot(self):
        self.hass.config.units = US_CUSTOMARY_SYSTEM
        self.runtime.refresh()
        self.assertFalse(self.runtime.snapshot.ac_available)
        await self.publish()
        self.assertTrue(self.runtime.snapshot.ac_available)
        self.assertEqual(self.runtime.snapshot.ac_target.value, 23)
        self.assertFalse(self.runtime.snapshot.ac_fault)
        stamp = self.runtime.snapshot.ac_mode.measured_at
        for _ in range(5):
            self.runtime.refresh()
        self.assertEqual(self.runtime.snapshot.ac_mode.measured_at, stamp)
        self.now += 15
        self.runtime.refresh()
        self.assertFalse(self.runtime.snapshot.ac_available)

    async def test_confirmed_command_requires_result_and_new_uart_not_climate(self):
        await self.publish()
        self.now += .1
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("climate", "set_temperature")])
        self.assertEqual(self.runtime.executor.planner.pending_timeout, 35)
        await self.result(status="accepted", request_id="mqtt-2", field="target")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        await self.settle()
        self.assertEqual(self.runtime.executor.last_command["status"], "awaiting_readback")
        await self.publish(1, published_uptime_ms=11000, age_ms=1000, request_id="mqtt-2")
        await self.result(status="confirmed", request_id="mqtt-2")
        self.assertEqual(self.runtime.executor.last_command["status"], "awaiting_readback")
        await self.publish(2, status_frames=12, published_uptime_ms=12000,
                           sample_uptime_ms=12000, target_temperature=22, request_id="mqtt-2")
        self.assertEqual(self.runtime.executor.last_command["status"], "confirmed")
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(len(self.calls), 1)

    async def test_esp_restart_during_pending_command_blocks_without_retry(self):
        await self.publish(); self.now += .1
        self.runtime.executor.enable(); await self.settle()
        await self.publish(1, boot_id="b"*32, status_frames=1, sample_uptime_ms=1000, published_uptime_ms=1000)
        self.assertEqual(self.runtime.executor.error, "HAIER_RESTART_DURING_COMMAND")
        self.assertEqual(len(self.calls), 1)

    async def test_fault_unknown_and_nonzero_never_allow_ac_start(self):
        await self.publish(fault_valid=False, fault=None, fault_code=None)
        self.assertIsNone(self.runtime.snapshot.ac_fault)
        self.assertFalse(self.runtime.snapshot.ac_fault_allows_operation)
        await self.publish(1, status_frames=11, sample_uptime_ms=11000, published_uptime_ms=11000,
                           fault=True, fault_code=255)
        self.assertTrue(self.runtime.snapshot.ac_fault)
        self.runtime.executor.enable(); await self.settle()
        self.assertNotIn(("climate", "set_temperature"), self.names())

    async def test_restored_and_removed_entity_do_not_fall_back_to_climate(self):
        await self.publish(restored=True)
        self.assertFalse(self.runtime.snapshot.ac_available)
        await self.publish(1, status_frames=11, sample_uptime_ms=11000, published_uptime_ms=11000)
        self.assertTrue(self.runtime.snapshot.ac_available)
        self.hass.states.async_remove("sensor.haier_data")
        await self.settle()
        self.assertFalse(self.runtime.snapshot.ac_available)
        self.assertFalse(self.runtime.snapshot.ac_unpowered)

    async def test_result_subscription_precedes_arm_and_unloads(self):
        self.runtime.stop()
        rt = Runtime(self.hass, SimpleNamespace(entry_id="test"), self.data)
        self.runtime = rt
        cancel = Mock()
        mqtt = ModuleType("homeassistant.components.mqtt")
        mqtt.async_subscribe = AsyncMock(return_value=cancel)
        with patch.dict(sys.modules, {"homeassistant.components.mqtt": mqtt}), patch(
                "custom_components.fourvrs_server_room.config_flow.validate_haier_binding"):
            await rt.async_prepare()
        mqtt.async_subscribe.assert_awaited_once_with(self.hass, "test-haier/result", rt._haier_result, qos=0)
        self.assertTrue(rt.haier_result_ready)
        rt.stop()
        cancel.assert_called_once()
        self.assertFalse(rt.haier_result_ready)

    async def test_binding_rejects_another_device_and_non_mqtt(self):
        climate = SimpleNamespace(platform="mqtt", device_id="one", unique_id="device_climate")
        sensor = SimpleNamespace(platform="mqtt", device_id="one", unique_id="device_telemetry")
        registry = SimpleNamespace(async_get=lambda name: climate if name.startswith("climate.") else sensor)
        with patch("homeassistant.helpers.entity_registry.async_get", return_value=registry):
            validate_haier_binding(self.hass, self.data)
            sensor.device_id = "two"
            with self.assertRaises(ConfigurationError):
                validate_haier_binding(self.hass, self.data)
            sensor.device_id = "one"; sensor.platform = "template"
            with self.assertRaises(ConfigurationError):
                validate_haier_binding(self.hass, self.data)

    async def test_profile_requires_exact_result_topic(self):
        for topic in ("", "#", "a/+/result", "a/state"):
            with self.assertRaises(ConfigurationError):
                validate(dict(self.data, haier_result_topic=topic))

    async def test_mode_and_target_are_two_independently_confirmed_commands(self):
        await self.publish(mode="HEAT_COOL")
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode")])
        await self.result(status="accepted", request_id="mqtt-2", field="hvac_mode")
        await self.publish(2, status_frames=12, sample_uptime_ms=12000, published_uptime_ms=12000,
                           request_id="mqtt-2")
        self.assertEqual(len(self.calls), 1)
        await self.result(status="confirmed", request_id="mqtt-2")
        self.assertEqual(self.names(), [("climate", "set_hvac_mode"), ("climate", "set_temperature")])
        self.assertNotIn("hvac_mode", self.calls[1][2])
        await self.result(status="accepted", request_id="mqtt-3", field="target")
        await self.result(status="confirmed", request_id="mqtt-2")
        self.assertFalse(self.runtime.executor.step.complete)
        await self.publish(4, status_frames=14, sample_uptime_ms=14000, published_uptime_ms=14000,
                           target_temperature=22, request_id="mqtt-3")
        await self.result(status="confirmed", request_id="mqtt-3")
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(len(self.calls), 2)

    async def test_ac_off_needs_command_evidence_before_ventilation_enable(self):
        self.hass.states.async_set("sensor.tout", "-2", {"unit_of_measurement": "°C"})
        await self.publish()
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode")])
        self.assertEqual(self.calls[0][2]["hvac_mode"], "off")
        await self.publish(2, mode="OFF", status_frames=12, sample_uptime_ms=12000,
                           published_uptime_ms=12000, request_id="mqtt-2")
        self.assertEqual(len(self.calls), 1)
        await self.result(status="accepted", request_id="mqtt-2", field="hvac_mode")
        await self.result(status="confirmed", request_id="mqtt-2")
        self.assertEqual(self.names(), [("climate", "set_hvac_mode"), ("number", "set_value")])
        self.assertEqual(self.calls[1][2]["value"], 1)

    async def test_live_ac_contradicts_power_loss_inferred_from_legacy_climate(self):
        self.hass.states.async_set("climate.ac", "unavailable")
        self.hass.states.async_set("binary_sensor.online", "off")
        self.hass.states.async_set("binary_sensor.battery", "on")
        await self.publish()
        self.assertFalse(self.runtime.snapshot.ac_unpowered)
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        self.assertNotIn(("switch", "turn_on"), self.names())
        self.assertNotIn(("number", "set_value"), self.names())

    async def test_manual_command_finishes_before_automatic_correction(self):
        await self.publish(command_state="pending")
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        self.assertEqual(self.runtime.executor.step.phase, "WAIT_HAIER_COMMAND")
        self.assertIsNone(self.runtime.executor.error)
        self.assertEqual(self.calls, [])
        await self.result(status="confirmed", request_id="mqtt-1")
        self.assertEqual(self.calls, [])
        await self.publish(2, status_frames=12, sample_uptime_ms=12000, published_uptime_ms=12000)
        self.assertEqual(self.names(), [("climate", "set_temperature")])
        self.assertIsNone(self.runtime.executor.error)
