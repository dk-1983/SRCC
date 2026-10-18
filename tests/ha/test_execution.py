"""Real HA services with simulated equipment and timestamped readbacks."""
import asyncio
import tempfile
import unittest
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.fourvrs_server_room.runtime import Runtime
from custom_components.fourvrs_server_room.configuration import validate
from custom_components.fourvrs_server_room.core.models import Mode
from types import SimpleNamespace


def settings(**changes):
    return dict(trm_temperature="sensor.tin", outdoor_temperature="sensor.tout",
                vent_enable="number.cdr", vent_output="switch.fan", climate="climate.ac",
                ups_online="binary_sensor.online", ups_on_battery="binary_sensor.battery",
                max_age=120, backup_max_skew=10, sample_tolerance=5, recovery_max_gap=70,
                reported_freshness_confirmed=True, control_profile_enabled=True,
                equipment_readback_confirmed=True, shared_mains_confirmed=True,
                ac_target=22, emergency_on=24, emergency_off=20, emergency_delta=0,
                command_timeout=5, condition_hold=0, minimum_dwell=0,
                **changes)


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir="/repo/.local")
        self.hass = HomeAssistant(self.tmp.name)
        self.calls = []
        self.feedback = True
        self.block = None
        self.hvac = {"temperature": 22, "hvac_modes": ["off", "cool", "auto"],
                     "min_temp": 16, "max_temp": 30}
        for entity_id, value in (("sensor.tin", "25"), ("sensor.tout", "12")):
            self.hass.states.async_set(entity_id, value, {"unit_of_measurement": "°C"})
        self.hass.states.async_set("number.cdr", "1")
        self.hass.states.async_set("switch.fan", "on")
        self.hass.states.async_set("climate.ac", "off", self.hvac)
        self.hass.states.async_set("binary_sensor.online", "on")
        self.hass.states.async_set("binary_sensor.battery", "off")
        for domain, services in (("number", ("set_value",)), ("switch", ("turn_on", "turn_off")),
                                 ("climate", ("set_hvac_mode", "set_temperature"))):
            for service in services:
                self.hass.services.async_register(domain, service, self.service)
        self.runtime = Runtime(self.hass, SimpleNamespace(entry_id="test"), validate(settings()))
        self.runtime.start()

    async def asyncTearDown(self):
        self.runtime.stop()
        if self.runtime.executor.task:
            await self.runtime.executor.task
        await self.hass.async_stop(force=True)
        self.tmp.cleanup()

    async def service(self, call):
        self.calls.append((call.domain, call.service, dict(call.data)))
        if self.block is not None:
            await self.block.wait()
        if not self.feedback:
            return
        entity_id = call.data["entity_id"]
        if call.domain == "number":
            self.hass.states.async_set(entity_id, str(call.data["value"]))
        elif call.domain == "switch":
            self.hass.states.async_set(entity_id, "on" if call.service == "turn_on" else "off")
        else:
            attributes = dict(self.hass.states.get(entity_id).attributes)
            attributes["temperature"] = call.data.get("temperature", attributes["temperature"])
            self.hass.states.async_set(entity_id, call.data.get("hvac_mode", self.hass.states.get(entity_id).state), attributes)

    async def settle(self):
        await self.hass.async_block_till_done()

    def names(self):
        return [(domain, service) for domain, service, _ in self.calls]

    async def test_startup_never_sends_and_enable_orders_cdr_fan_ac(self):
        await self.settle()
        self.assertFalse(self.runtime.executor.enabled)
        self.assertEqual(self.calls, [])
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value"), ("switch", "turn_off"),
                                       ("climate", "set_hvac_mode")])
        self.assertEqual(self.calls[-1][2]["hvac_mode"], "cool")
        self.assertNotIn("temperature", self.calls[-1][2])
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_cool_to_automatic_vent_stops_ac_first(self):
        self.hass.states.async_set("sensor.tout", "-2", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode"), ("number", "set_value")])
        self.assertEqual(self.calls[0][2]["hvac_mode"], "off")
        self.assertEqual(self.calls[1][2]["value"], 1)

    async def test_missing_readback_times_out_without_next_command_or_retry(self):
        self.feedback = False
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value")])
        with patch("custom_components.fourvrs_server_room.runtime.monotonic",
                   return_value=self.runtime._clock_anchor + 6):
            self.runtime.refresh()
            await self.settle()
        self.assertEqual(self.runtime.executor.error, "READBACK_TIMEOUT")
        self.runtime.refresh()
        await self.settle()
        self.assertEqual(len(self.calls), 1)

    async def test_battery_manual_fan_and_mains_return(self):
        self.hass.states.async_set("sensor.tout", "5", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "unavailable")
        self.hass.states.async_set("binary_sensor.online", "off")
        self.hass.states.async_set("binary_sensor.battery", "on")
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value"), ("switch", "turn_on")])
        self.assertTrue(self.runtime.snapshot.ac_unpowered)
        self.calls.clear()
        self.hass.states.async_set("binary_sensor.online", "on")
        self.hass.states.async_set("binary_sensor.battery", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        await self.settle()
        self.assertFalse(self.runtime.snapshot.ac_unpowered)
        self.assertEqual(self.names(), [("switch", "turn_off")])
        self.assertIs(self.runtime.status.decision.desired, Mode.AIR_CONDITIONING)

    async def test_battery_fan_stops_at_ten_degrees(self):
        self.hass.states.async_set("sensor.tout", "10", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("climate.ac", "unavailable")
        self.hass.states.async_set("binary_sensor.online", "off")
        self.hass.states.async_set("binary_sensor.battery", "on")
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value"), ("switch", "turn_off")])

    async def test_unavailable_ac_on_mains_does_not_allow_fan_start(self):
        self.hass.states.async_set("sensor.tout", "-2", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "unavailable")
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.runtime.executor.step.reason, "AC_OFF_UNPROVEN")

    async def test_disable_during_awaited_service_prevents_next_command(self):
        self.block = asyncio.Event()
        self.runtime.executor.enable()
        for _ in range(30):
            await asyncio.sleep(0)
            if self.calls:
                break
        self.assertEqual(len(self.calls), 1)
        self.runtime.executor.disable()
        self.block.set()
        await self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertFalse(self.runtime.executor.enabled)

    async def test_service_exception_latches_and_does_not_retry(self):
        async def fail(call):
            raise HomeAssistantError("SIMULATED_FAILURE")
        self.hass.services.async_register("number", "set_value", fail)
        self.runtime.executor.enable()
        await self.settle()
        self.assertIn("SIMULATED_FAILURE", self.runtime.executor.error)
        self.assertEqual(self.runtime.executor.step.phase, "ERROR")

    async def test_shared_power_evidence_requires_explicit_profile_and_fresh_signals(self):
        self.hass.states.async_set("climate.ac", "unavailable")
        self.hass.states.async_set("binary_sensor.online", "off")
        self.hass.states.async_set("binary_sensor.battery", "on")
        self.runtime.refresh()
        self.assertTrue(self.runtime.snapshot.ac_unpowered)
        self.runtime.data["shared_mains_confirmed"] = False
        self.runtime.refresh()
        self.assertFalse(self.runtime.snapshot.ac_unpowered)
        self.runtime.data["shared_mains_confirmed"] = True
        with patch("custom_components.fourvrs_server_room.runtime.monotonic",
                   return_value=self.runtime._clock_anchor + 121):
            self.runtime.refresh()
        self.assertFalse(self.runtime.snapshot.ac_unpowered)

    async def test_legacy_minimum_off_does_not_delay_ac(self):
        self.runtime.data["minimum_ac_off"] = 180
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value"), ("switch", "turn_off"),
                                       ("climate", "set_hvac_mode")])
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_source_loss_during_cdr_command_prevents_cool(self):
        original = self.service
        async def lose_temperature(call):
            await original(call)
            self.hass.states.async_set("sensor.tin", "unavailable")
        self.hass.services.async_register("number", "set_value", lose_temperature)
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value")])
        self.assertEqual(self.runtime.executor.step.reason, "TEMPERATURE_UNTRUSTED")

    async def test_battery_threshold_hysteresis(self):
        self.hass.states.async_set("sensor.tout", "5", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "unavailable")
        self.hass.states.async_set("binary_sensor.online", "off")
        self.hass.states.async_set("binary_sensor.battery", "on")
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.hass.states.get("switch.fan").state, "on")
        for value, expected in ((23, "on"), (20, "off"), (23, "off"), (24, "on")):
            self.hass.states.async_set("sensor.tin", str(value), {"unit_of_measurement": "°C"})
            await self.settle()
            self.assertEqual(self.hass.states.get("switch.fan").state, expected)

    async def test_turning_off_does_not_undo_last_equipment_state(self):
        self.runtime.executor.enable()
        await self.settle()
        count = len(self.calls)
        self.runtime.executor.disable()
        self.hass.states.async_set("sensor.tout", "-3", {"unit_of_measurement": "°C"})
        await self.settle()
        self.assertEqual(len(self.calls), count)
        self.assertEqual(self.hass.states.get("climate.ac").state, "cool")

    async def test_no_output_control_before_permission_readback(self):
        self.feedback = False
        self.runtime.executor.enable()
        await self.settle()
        self.hass.states.async_set("number.cdr", "unavailable")
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value")])

    async def test_unconfigured_profile_cannot_be_armed(self):
        self.runtime.data["control_profile_enabled"] = False
        with self.assertRaises(HomeAssistantError):
            self.runtime.executor.enable()
        self.assertFalse(self.runtime.executor.enabled)
        self.assertEqual(self.calls, [])

    async def test_unload_waits_for_inflight_command_and_sends_nothing_else(self):
        from custom_components.fourvrs_server_room import async_unload_entry
        from unittest.mock import AsyncMock
        self.block = asyncio.Event()
        self.runtime.executor.enable()
        for _ in range(30):
            await asyncio.sleep(0)
            if self.calls:
                break
        entry = SimpleNamespace(runtime_data=self.runtime)
        self.hass.config_entries = SimpleNamespace(async_unload_platforms=AsyncMock(return_value=True))
        unloading = asyncio.create_task(async_unload_entry(self.hass, entry))
        await asyncio.sleep(0)
        self.assertTrue(self.runtime.stopped)
        self.assertFalse(unloading.done())
        self.block.set()
        self.assertTrue(await unloading)
        self.assertEqual(len(self.calls), 1)
        self.hass.config_entries = None

    async def test_control_form_and_fahrenheit_target(self):
        from custom_components.fourvrs_server_room.config_flow import schema
        from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
        data = validate(schema()(settings()))
        self.assertTrue(data["control_profile_enabled"])
        self.hass.config.units = US_CUSTOMARY_SYSTEM
        self.hvac.update(temperature=70, min_temp=60, max_temp=90)
        self.hass.states.async_set("climate.ac", "off", self.hvac)
        self.runtime.executor.enable()
        await self.settle()
        self.assertAlmostEqual(self.calls[-1][2]["temperature"], 71.6)
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_negative_threshold_form_and_legacy_recovery_migration(self):
        from custom_components.fourvrs_server_room.config_flow import schema
        data = settings(ac_hard_min=-15, ac_to_vent=-4, vent_to_ac=-2)
        self.assertEqual(validate(schema()(data))["vent_to_ac"], -2)
        self.assertNotIn("recovery_hold", validate(dict(data, recovery_hold=120)))

    async def test_reserve_hold_survives_reload_and_needs_explicit_retry(self):
        from unittest.mock import AsyncMock
        self.runtime.observer.reserve_hold = True
        self.runtime.refresh()
        await self.settle()
        stored = await self.runtime.reserve_store.async_load()
        self.assertEqual(stored, {"hold": True})
        other = Runtime(self.hass, SimpleNamespace(entry_id="test"), self.runtime.data)
        other.voice.async_restore = AsyncMock()
        await other.async_prepare()
        self.assertTrue(other.observer.reserve_hold)
        self.hass.states.async_set("sensor.tin", "22", {"unit_of_measurement": "°C"})
        await self.settle()
        await self.runtime.async_set_control(False)
        await self.runtime.async_set_control(True)
        await self.settle()
        self.assertFalse(self.runtime.observer.reserve_hold)

    async def test_recovery_offset_cannot_reach_warning(self):
        from custom_components.fourvrs_server_room.configuration import ConfigurationError
        self.assertEqual(validate(settings(rack_recovery_offset=2))["rack_recovery_offset"], 2)
        for offset in (-1, 4, float("nan")):
            with self.assertRaises(ConfigurationError):
                validate(settings(rack_recovery_offset=offset))

    async def test_recovery_ignores_ac_inlet_and_holds_ac_after_rack_recovery(self):
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, current_temperature=23))
        self.hass.states.async_set("sensor.tin", "28", {"unit_of_measurement": "°C"})
        await self.settle()
        self.assertTrue(self.runtime.status.alarm.thermal_critical)
        self.hass.states.async_set("sensor.tin", "26.8", {"unit_of_measurement": "°C"})
        await self.settle()
        # Target is already 22, but measured air remains warmer.
        self.assertTrue(self.runtime.status.alarm.thermal_critical)
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, current_temperature=16.5))
        await self.settle()
        self.assertTrue(self.runtime.status.alarm.thermal_critical)
        self.hass.states.async_set("sensor.tin", "22", {"unit_of_measurement": "°C"})
        await self.settle()
        self.assertFalse(self.runtime.status.alarm.thermal_critical)
        self.assertTrue(self.runtime.observer.reserve_hold)
        self.assertEqual(self.calls, [])

    async def test_ac_measured_temperature_is_converted_from_fahrenheit(self):
        from homeassistant.util.unit_system import US_CUSTOMARY_SYSTEM
        self.hass.config.units = US_CUSTOMARY_SYSTEM
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, current_temperature=71.6))
        await self.settle()
        self.assertAlmostEqual(self.runtime.snapshot.ac_temperature.value, 22)

    async def test_switch_permission_profile(self):
        self.runtime.stop()
        self.hass.states.async_set("switch.cdr", "on")
        data = settings()
        data["vent_enable"] = "switch.cdr"
        self.runtime = Runtime(self.hass, SimpleNamespace(entry_id="test"), validate(data))
        self.runtime.start()
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("switch", "turn_off"), ("switch", "turn_off"),
                                       ("climate", "set_hvac_mode")])
        self.assertEqual(self.calls[0][2]["entity_id"], "switch.cdr")
        self.assertEqual(self.calls[1][2]["entity_id"], "switch.fan")

    async def test_out_of_range_target_blocks_ac_command(self):
        self.runtime.data["ac_target"] = 15
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.runtime.executor.error, "AC_TARGET_OUT_OF_RANGE")
        self.assertNotIn(("climate", "set_temperature"), self.names())

    async def test_command_journal_distinguishes_send_service_and_readback(self):
        from custom_components.fourvrs_server_room.diagnostics import async_get_config_entry_diagnostics
        self.runtime.executor.enable()
        await self.settle()
        events = list(self.runtime.telemetry.events)
        for action in ("vent_disable", "fan_off", "ac_cool"):
            kinds = [item["event"] for item in events if item.get("action") == action]
            self.assertEqual(kinds, ["command_sent", "service_completed", "command_confirmed"])
        self.assertEqual(self.runtime.executor.last_command["status"], "confirmed")
        report = await async_get_config_entry_diagnostics(self.hass, SimpleNamespace(runtime_data=self.runtime))
        for identifier in ("number.cdr", "switch.fan", "climate.ac", "sensor.tin"):
            self.assertNotIn(identifier, str(report))

    async def test_input_age_journal_deduplicates_and_records_recovery(self):
        with patch("custom_components.fourvrs_server_room.runtime.monotonic",
                   return_value=self.runtime._clock_anchor + 181):
            for _ in range(5):
                self.runtime.refresh()
        events = [item for item in self.runtime.telemetry.events
                  if item["event"] == "input_quality" and item["role"] == "climate"]
        self.assertEqual([item["quality"] for item in events], ["valid", "stale"])
        self.assertGreater(events[-1]["publication_age_s"], 180)
        self.assertEqual(events[-1]["max_age_s"], 180)
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        await self.settle()
        events = [item for item in self.runtime.telemetry.events
                  if item["event"] == "input_quality" and item["role"] == "climate"]
        self.assertEqual(events[-1]["quality"], "valid")
        self.assertEqual(self.calls, [])

    async def restart_runtime(self):
        old = self.runtime
        old.stop()
        if old.executor.task:
            await old.executor.task
        self.runtime = Runtime(self.hass, old.entry, old.data)
        self.runtime.start()
        await self.runtime.async_restore_control()
        await self.settle()

    async def test_saved_on_survives_runtime_shutdown_and_reconciles(self):
        await self.runtime.async_set_control(True)
        await self.settle()
        self.calls.clear()
        await self.restart_runtime()
        self.assertTrue(self.runtime.executor.enabled)
        self.assertEqual(self.calls, [])
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_explicit_off_is_preserved_on_restart(self):
        await self.runtime.async_set_control(True)
        await self.settle()
        await self.runtime.async_set_control(False)
        self.calls.clear()
        await self.restart_runtime()
        self.assertFalse(self.runtime.executor.enabled)
        self.assertEqual(self.calls, [])

    async def test_restored_on_waits_for_missing_inputs(self):
        await self.runtime.async_set_control(True)
        await self.settle()
        self.hass.states.async_set("sensor.tin", "unavailable")
        self.hass.states.async_set("climate.ac", "unavailable")
        self.calls.clear()
        await self.restart_runtime()
        self.assertTrue(self.runtime.executor.enabled)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.runtime.executor.step.phase, "BLOCKED")

    async def test_disabled_profile_clears_saved_on(self):
        await self.runtime.async_set_control(True)
        await self.settle()
        self.runtime.data["control_profile_enabled"] = False
        self.calls.clear()
        await self.restart_runtime()
        self.assertFalse(self.runtime.executor.enabled)
        self.assertEqual(await self.runtime.control_store.async_load(), {"enabled": False})
        self.assertEqual(self.calls, [])

    async def test_ac_publication_limit_separate_from_other_roles(self):
        from datetime import timedelta
        from homeassistant.util import dt as dt_util
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        self.hass.states.get("climate.ac").last_reported = dt_util.utcnow() - timedelta(seconds=160)
        self.runtime.refresh()
        self.assertEqual(self.runtime.input_status["climate"]["quality"], "valid")
        self.assertEqual(self.runtime.input_status["climate"]["max_age_s"], 180)
        self.assertEqual(self.runtime.reported_mode, "air_conditioning")
        self.assertTrue(self.runtime.snapshot.ac_target.fresh(self.runtime.snapshot.now, 120))
        self.hass.states.get("binary_sensor.online").last_reported = dt_util.utcnow() - timedelta(seconds=160)
        self.runtime.refresh()
        self.assertEqual(self.runtime.input_status["ups_online"]["quality"], "stale")
        self.hass.states.get("climate.ac").last_reported = dt_util.utcnow() - timedelta(seconds=181)
        self.runtime.refresh()
        self.assertEqual(self.runtime.input_status["climate"]["quality"], "stale")
        self.assertEqual(self.runtime.reported_mode, "unknown")

    async def test_running_ac_setpoint_drift_corrected_without_seasonal_dwell(self):
        self.runtime.data.update(condition_hold=30, minimum_dwell=300, minimum_ac_off=180)
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.calls, [])
        self.assertEqual(self.runtime.executor.step.phase, "COMPLETE")
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, temperature=23))
        await self.settle()
        self.assertEqual(self.names(), [("climate", "set_temperature")])
        self.assertEqual(self.calls[0][2]["temperature"], 22)
        self.assertNotIn("hvac_mode", self.calls[0][2])
        self.assertEqual(self.runtime.executor.last_command["status"], "confirmed")
        self.runtime.refresh()
        await self.settle()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.runtime.executor.step.phase, "COMPLETE")

    async def test_setpoint_drift_does_not_override_manual_or_fault(self):
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, temperature=23))
        await self.settle()
        self.assertEqual(self.calls, [])
        self.runtime.data["ac_fault"] = "binary_sensor.ac_fault"
        self.hass.states.async_set("binary_sensor.ac_fault", "on")
        self.runtime.executor.enable()
        await self.settle()
        self.assertNotIn(("climate", "set_temperature"), self.names())

    async def test_seasonal_change_still_waits_for_condition_hold(self):
        self.runtime.data.update(condition_hold=30, minimum_dwell=300, minimum_ac_off=180)
        # Existing automatic ventilation with warm outdoor air needs a new mode.
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.runtime.executor.step.phase, "WAIT_TIMING")
        self.assertEqual(self.calls, [])

    async def test_critical_vent_to_ac_bypasses_seasonal_timers_in_order(self):
        self.runtime.data.update(condition_hold=30, minimum_dwell=300)
        self.hass.states.async_set("sensor.tin", "28", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("sensor.tout", "-1", {"unit_of_measurement": "°C"})
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value"), ("switch", "turn_off"),
                                       ("climate", "set_hvac_mode")])
        self.assertEqual(self.calls[-1][2]["hvac_mode"], "cool")
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_idle_recovery_does_not_wait_for_seasonal_dwell(self):
        self.runtime.data.update(condition_hold=30, minimum_dwell=300)
        self.hass.states.async_set("sensor.tout", "-1", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.runtime.executor.enable()
        self.runtime.executor.timing.confirmed(now=self.runtime.snapshot.now)
        await self.settle()
        self.assertEqual(self.names(), [("number", "set_value")])
        self.assertTrue(self.runtime.executor.step.complete)
        committed = self.runtime.executor.timing.committed_at
        self.hass.states.async_set("binary_sensor.online", "unavailable")
        await self.settle()
        self.assertEqual(self.runtime.executor.step.phase, "BLOCKED")
        self.hass.states.async_set("binary_sensor.online", "on")
        await self.settle()
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(self.runtime.executor.timing.committed_at, committed)
        self.assertEqual(len(self.calls), 1)

    async def test_legacy_ac_pause_cannot_delay_critical_cooling(self):
        self.runtime.data["minimum_ac_off"] = 180
        self.hass.states.async_set("sensor.tin", "28", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("sensor.tout", "-1", {"unit_of_measurement": "°C"})
        self.runtime.executor.enable()
        await self.settle()
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertIn(("climate", "set_hvac_mode"), self.names())

    async def delayed_ac_setup(self):
        self.runtime.data.update(command_timeout=30, ac_readback_timeout=180)
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", dict(self.hvac, temperature=23))
        self.feedback = False
        self.runtime.executor.enable()
        await self.settle()
        return self.runtime.executor.planner.sent_at

    async def at_elapsed(self, sent, elapsed, *, feedback=False):
        from datetime import timedelta
        now = sent + elapsed
        stamp = self.runtime._wall_anchor + timedelta(seconds=now-self.runtime._clock_anchor-0.001)
        with patch("custom_components.fourvrs_server_room.runtime.monotonic", return_value=now):
            # Simulated heartbeat keeps unrelated guards independently fresh.
            for entity_id in ("sensor.tin", "sensor.tout", "number.cdr", "switch.fan",
                              "binary_sensor.online", "binary_sensor.battery"):
                self.hass.states.get(entity_id).last_reported = stamp
            if feedback:
                self.hass.states.async_set("climate.ac", "cool", self.hvac)
                self.hass.states.get("climate.ac").last_reported = stamp
            self.runtime.refresh()
            await self.settle()

    async def test_ac_readback_after_160_seconds_is_confirmed_without_retry(self):
        sent = await self.delayed_ac_setup()
        await self.at_elapsed(sent, 31)
        self.assertIsNone(self.runtime.executor.error)
        self.assertEqual(self.runtime.executor.step.phase, "WAIT_READBACK")
        self.assertEqual(self.runtime.executor.wait_details["readback_remaining_s"], 149)
        await self.at_elapsed(sent, 160, feedback=True)
        self.assertEqual(self.runtime.executor.last_command["status"], "confirmed")
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(len(self.calls), 1)

    async def test_ac_timeout_after_180_seconds_latches_without_retry(self):
        sent = await self.delayed_ac_setup()
        await self.at_elapsed(sent, 179)
        self.assertIsNone(self.runtime.executor.error)
        await self.at_elapsed(sent, 181)
        self.assertEqual(self.runtime.executor.error, "READBACK_TIMEOUT")
        await self.at_elapsed(sent, 190, feedback=True)
        self.assertEqual(self.runtime.executor.error, "READBACK_TIMEOUT")
        self.assertEqual(len(self.calls), 1)

    async def test_auto_mode_then_target_are_separate_confirmed_commands(self):
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "auto", dict(self.hvac, temperature=23))
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode"), ("climate", "set_temperature")])
        self.assertEqual(self.calls[0][2], {"entity_id": "climate.ac", "hvac_mode": "cool"})
        self.assertEqual(self.calls[1][2], {"entity_id": "climate.ac", "temperature": 22})
        self.assertTrue(self.runtime.executor.step.complete)

    async def test_missing_mode_confirmation_never_sends_temperature(self):
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "auto", dict(self.hvac, temperature=23))
        self.feedback = False
        self.runtime.executor.enable()
        await self.settle()
        self.assertEqual(self.names(), [("climate", "set_hvac_mode")])
        sent = self.runtime.executor.planner.sent_at
        await self.at_elapsed(sent, 2)
        self.assertIsNone(self.runtime.executor.error)
        await self.at_elapsed(sent, 3.1)
        self.assertEqual(self.runtime.executor.error, "READBACK_TIMEOUT")
        self.assertEqual(len(self.calls), 1)
