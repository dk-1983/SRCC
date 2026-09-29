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
    # Match MQTT_ATTRIBUTES_BLOCKED in HA: entity attributes omit available.
    data.pop("available", None)
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
        await self.settle()  # Drain fixture events before subscribing to live telemetry.
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
        self.now += 15.01
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
        await self.publish(.1)
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode")], str(self.runtime.status))
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

    async def test_deployed_telemetry_without_reserved_available_is_usable(self):
        await self.publish(status_frames=1736, sample_uptime_ms=2488752,
                           published_uptime_ms=2488855, age_ms=103,
                           target_temperature=22, command_state="not_started", request_id="")
        self.assertTrue(self.runtime.snapshot.ac_available)
        self.assertFalse(self.runtime.snapshot.ac_fault)
        self.assertEqual(self.runtime.reported_mode, "air_conditioning")
        self.assertEqual(self.runtime.status.decision.reason, "HOLD_AC")
        self.assertAlmostEqual(self.runtime.haier.diagnostics(self.now)["source_age_lower_bound_s"], .103)
        self.runtime.executor.enable(); await self.settle()
        self.assertEqual(self.calls, [])

    async def test_missing_available_never_defaults_to_true_for_untrusted_fault(self):
        for value in (False, None, "true", 1):
            attributes = packet(fault_valid=value, fault=None, fault_code=None)
            self.hass.states.async_set("sensor.haier_data", "10", attributes, force_update=True)
            await self.settle()
            self.assertFalse(self.runtime.snapshot.ac_available)
            self.assertFalse(self.runtime.snapshot.ac_fault_allows_operation)
        self.assertEqual(self.runtime.input_status["ac_telemetry"]["quality"], "invalid")
        self.assertEqual(self.runtime.input_status["ac_telemetry"]["telemetry_reason"], "INVALID_FLAGS")

    async def test_explicit_available_false_is_not_overridden(self):
        attributes = packet()
        attributes["available"] = False
        self.hass.states.async_set("sensor.haier_data", "10", attributes, force_update=True)
        await self.settle()
        self.assertFalse(self.runtime.snapshot.ac_available)
        self.assertEqual(self.runtime.haier.invalid_reason, "INVALID_FAULT")

    async def conflict_setup(self):
        await self.publish()
        self.now += .1
        self.runtime.executor.enable(); await self.settle()
        await self.result(status="accepted", request_id="mqtt-2", field="target")
        await self.result(status="accepted", request_id="mqtt-3", field="target")
        self.assertIsNone(self.runtime.executor.error)
        self.assertEqual(self.runtime.executor.step.reason, "COMMAND_CONFLICT_RECOVERY")
        self.assertEqual(self.runtime.executor.last_command["status"], "conflict")
        self.assertEqual(len(self.calls), 1)

    async def recovery_frame(self, elapsed=36, **changes):
        values = dict(status_frames=10+int(elapsed), sample_uptime_ms=10000+int(elapsed*1000),
                      published_uptime_ms=10000+int(elapsed*1000), request_id="mqtt-3")
        values.update(changes)
        await self.publish(elapsed, **values)

    async def test_conflict_recovers_and_corrects_target_with_independent_confirmation(self):
        await self.conflict_setup()
        await self.recovery_frame(2, target_temperature=21)
        self.assertEqual(len(self.calls), 1)
        await self.recovery_frame(target_temperature=21)
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.calls[-1][2], {"entity_id": "climate.ac", "temperature": 22})
        await self.result(status="accepted", request_id="mqtt-4", field="target")
        await self.result(status="confirmed", request_id="mqtt-4")
        await self.recovery_frame(38, target_temperature=22, request_id="mqtt-4")
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(self.runtime.executor.last_command["status"], "confirmed")
        events = [e["event"] for e in self.runtime.telemetry.events]
        self.assertIn("command_conflict", events)
        self.assertIn("command_reconciliation", events)
        self.assertEqual(events.count("command_confirmed"), 1)

    async def recover_mode(self, mode):
        await self.conflict_setup()
        await self.recovery_frame(mode=mode, target_temperature=21)
        self.assertEqual(self.calls[-1][2], {"entity_id": "climate.ac", "hvac_mode": "cool"})
        await self.result(status="accepted", request_id="mqtt-4", field="hvac_mode")
        await self.result(status="confirmed", request_id="mqtt-4")
        await self.recovery_frame(38, request_id="mqtt-4", target_temperature=21)
        self.assertEqual(self.calls[-1][2], {"entity_id": "climate.ac", "temperature": 22})
        self.assertEqual(len(self.calls), 3)

    async def test_conflict_needs_new_uart_not_heartbeat_or_pending(self):
        await self.conflict_setup()
        await self.publish(36, published_uptime_ms=46000, age_ms=36000, request_id="mqtt-3")
        self.assertEqual(len(self.calls), 1)
        await self.recovery_frame(37, command_state="pending")
        self.assertEqual(len(self.calls), 1)
        await self.result(status="confirmed", request_id="mqtt-3")
        await self.recovery_frame(38)
        self.assertEqual(len(self.calls), 1)  # Quiet period after result.
        await self.recovery_frame(43)
        self.assertEqual(len(self.calls), 2)

    async def test_conflict_does_not_resend_already_achieved_target(self):
        await self.conflict_setup()
        await self.recovery_frame(target_temperature=22)
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertNotEqual(self.runtime.executor.last_command["status"], "confirmed")

    async def test_conflict_recovery_keeps_compressor_delay(self):
        await self.conflict_setup()
        self.runtime.data["minimum_ac_off"] = 180
        await self.recovery_frame(mode="FAN_ONLY")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.runtime.executor.step.phase, "WAIT_AC_MINIMUM_OFF")

    async def test_conflict_recovery_does_not_bypass_fault_or_manual_off(self):
        await self.conflict_setup()
        await self.recovery_frame(fault=True, fault_code=1)
        # A fault may request OFF; it must never start COOL or correct a target.
        self.assertTrue(all(c[2].get("hvac_mode") == "off" for c in self.calls[1:]))
        count = len(self.calls)
        self.runtime.executor.disable()
        await self.recovery_frame(38)
        self.assertEqual(len(self.calls), count)

    async def test_conflict_followed_by_restart_stays_latched(self):
        await self.conflict_setup()
        await self.recovery_frame(36, boot_id="b"*32)
        self.assertEqual(self.runtime.executor.error, "HAIER_RESTART_DURING_COMMAND")
        self.assertEqual(len(self.calls), 1)

    async def test_conflict_followed_by_firmware_timeout_stays_latched(self):
        await self.conflict_setup()
        await self.recovery_frame(36, command_state="timeout_unconfirmed")
        self.assertEqual(self.runtime.executor.error, "HAIER_COMMAND_UNCONFIRMED")
        self.assertEqual(len(self.calls), 1)

    async def test_conflict_recovers_fan_mode_then_target(self):
        await self.recover_mode("FAN_ONLY")

    async def test_conflict_recovers_heat_mode_then_target(self):
        await self.recover_mode("HEAT")

    async def test_conflict_recovers_auto_mode_then_target(self):
        await self.recover_mode("AUTO")

    async def test_conflict_recovers_dry_mode_then_target(self):
        await self.recover_mode("DRY")

    async def test_conflict_recovers_off_mode_then_target(self):
        await self.recover_mode("OFF")

    async def test_repeated_conflicts_are_rate_limited_and_can_recover(self):
        await self.conflict_setup()
        await self.recovery_frame(36)
        self.assertEqual(len(self.calls), 2)
        await self.result(status="accepted", request_id="mqtt-4", field="target")
        await self.result(status="accepted", request_id="mqtt-5", field="target")
        await self.recovery_frame(40, request_id="mqtt-5")
        self.assertEqual(len(self.calls), 2)
        await self.recovery_frame(72, request_id="mqtt-5")
        self.assertEqual(len(self.calls), 3)
        self.assertIsNone(self.runtime.executor.error)
