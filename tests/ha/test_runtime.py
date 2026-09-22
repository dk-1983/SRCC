"""Integration tests using actual HA classes in the local hassfest image.

Run with PYTHONPATH=/repo:/usr/src/homeassistant and unittest discovery -s tests/ha.
Only platform forwarding is mocked in lifecycle tests, not HA states or events.
"""
import tempfile
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry, ConfigEntries
from homeassistant.util import dt as dt_util

from custom_components.fourvrs_server_room import async_setup_entry, async_unload_entry
from custom_components.fourvrs_server_room.configuration import validate
from custom_components.fourvrs_server_room.config_flow import schema, validate_with_hass, ConfigFlow
from custom_components.fourvrs_server_room.runtime import Runtime
from custom_components.fourvrs_server_room.sensor import SrccSensor
from custom_components.fourvrs_server_room.binary_sensor import SrccBinarySensor
from custom_components.fourvrs_server_room.diagnostics import async_get_config_entry_diagnostics
from custom_components.fourvrs_server_room.core.models import Severity


def settings(**changes):
    return {"trm_temperature": "sensor.trm", "outdoor_temperature": "sensor.outdoor",
            "bolid_temperature_1": "sensor.b1", "bolid_temperature_2": "sensor.b2",
            "max_age": 120, "backup_max_skew": 10, "sample_tolerance": 5,
            "recovery_max_gap": 70, "reported_freshness_confirmed": True, **changes}


class HaRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="srcc-ha-", dir="/repo/.local")
        self.hass = HomeAssistant(self.directory.name)
        self.hass.config_entries = ConfigEntries(self.hass, {})
        from homeassistant.helpers import entity_registry, device_registry
        device_registry.async_setup(self.hass)
        await device_registry.async_load(self.hass, load_empty=True)
        await entity_registry.async_load(self.hass, load_empty=True)
        self.hass.states.async_set("sensor.trm", "25", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("sensor.outdoor", "-2", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("sensor.b1", "27", {"unit_of_measurement": "°C"})
        self.hass.states.async_set("sensor.b2", "29", {"unit_of_measurement": "°C"})
        self.runtime = Runtime(self.hass, SimpleNamespace(entry_id="test"), settings())

    async def asyncTearDown(self):
        self.runtime.stop()
        await self.hass.async_stop(force=True)
        self.directory.cleanup()

    async def test_runtime_real_state_events_and_single_backup_warning(self):
        self.runtime.start()
        self.assertEqual(self.runtime.status.temperature.sample.value, 25)
        self.hass.states.async_set("sensor.trm", "unavailable")
        await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.status.temperature.sample.value, 28)
        self.assertEqual(self.runtime.status.alarm.severity, Severity.CRITICAL)
        self.hass.states.async_set("sensor.b2", "unavailable")
        await self.hass.async_block_till_done()
        self.assertEqual(self.runtime.status.temperature.sample.value, 27)
        self.assertIn("BACKUP_UNAVAILABLE:sensor.b2", self.runtime.status.warnings)
        self.assertEqual(self.runtime.status.alarm.severity, Severity.CRITICAL)

    async def test_no_service_calls_and_unload_cleans_callbacks(self):
        service_calls = AsyncMock()
        patcher = patch.object(type(self.hass.services), "async_call", service_calls)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.runtime.start()
        self.assertEqual(len(self.runtime.unsubscribers), 2)
        self.runtime.stop()
        previous = self.runtime.status
        self.hass.states.async_set("sensor.trm", "29", {"unit_of_measurement": "°C"})
        await self.hass.async_block_till_done()
        self.assertIs(self.runtime.status, previous)
        self.assertEqual(self.runtime.unsubscribers, [])
        service_calls.assert_not_called()

    async def test_sensor_values_and_diagnostics(self):
        self.runtime.start()
        self.assertEqual(SrccSensor(self.runtime, "temperature").native_value, 25)
        self.assertIs(SrccBinarySensor(self.runtime, "observation_only").is_on, True)
        report = await async_get_config_entry_diagnostics(self.hass, SimpleNamespace(runtime_data=self.runtime))
        self.assertNotIn("sensor.trm", str(report))
        self.assertTrue(report["observation_only"])

    async def test_watchdog_detects_old_data(self):
        self.runtime.start()
        with patch("custom_components.fourvrs_server_room.runtime.monotonic",
                   return_value=self.runtime._clock_anchor + 121):
            self.runtime.refresh()
        self.assertIsNone(self.runtime.status.temperature.sample)

    async def test_schema_and_flow_validate_actual_states(self):
        data = schema()(settings())
        self.assertEqual(validate_with_hass(self.hass, data)["critical_on"], 28)
        flow = ConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_user(data)
        self.assertEqual(result["type"], "create_entry")
        self.assertEqual(result["data"]["trm_temperature"], "sensor.trm")

    async def test_bad_form_returns_error_without_commands(self):
        flow = ConfigFlow()
        flow.hass = self.hass
        result = await flow.async_step_user(settings(critical_on=25))
        self.assertEqual(result["errors"]["base"], "invalid_thresholds")

    async def test_real_config_entry_lifecycle(self):
        entry = ConfigEntry(version=1, minor_version=1, domain="fourvrs_server_room",
                            title="SRCC", data=settings(), options={}, source="user",
                            unique_id=None, discovery_keys={}, subentries_data=None)
        self.hass.config_entries.async_forward_entry_setups = AsyncMock()
        self.hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
        self.assertTrue(await async_setup_entry(self.hass, entry))
        self.assertIsNotNone(entry.runtime_data.status)
        self.assertTrue(await async_unload_entry(self.hass, entry))
        self.assertTrue(entry.runtime_data.stopped)

    async def test_full_platform_setup_creates_entities(self):
        from pathlib import Path
        from homeassistant import loader
        from homeassistant.helpers import entity, frame, translation, issue_registry
        from homeassistant.config_entries import ConfigEntryState
        Path(self.directory.name, "custom_components").symlink_to("/repo/custom_components", target_is_directory=True)
        loader.async_setup(self.hass)
        entity.async_setup(self.hass)
        frame.async_setup(self.hass)
        translation.async_setup(self.hass)
        await issue_registry.async_load(self.hass, load_empty=True)
        entry = ConfigEntry(version=1, minor_version=1, domain="fourvrs_server_room",
                            title="SRCC", data=settings(), options={}, source="user",
                            unique_id=None, discovery_keys={}, subentries_data=None)
        await self.hass.config_entries.async_add(entry)
        await self.hass.async_block_till_done()
        self.assertEqual(entry.state, ConfigEntryState.LOADED)
        entities = [state for state in self.hass.states.async_all()
                    if state.entity_id.startswith(("sensor.server_room_climate_controller_",
                                                   "binary_sensor.server_room_climate_controller_"))]
        self.assertEqual(len(entities), 15, [state.entity_id for state in self.hass.states.async_all()])
        loaded_runtime = entry.runtime_data
        options = settings()
        del options["bolid_temperature_1"]
        del options["bolid_temperature_2"]
        self.hass.config_entries.async_update_entry(entry, options=options)
        await self.hass.async_block_till_done()
        self.assertTrue(loaded_runtime.stopped)
        self.assertNotIn("bolid_temperature_1", entry.runtime_data.data)
        loaded_runtime = entry.runtime_data
        self.assertTrue(await self.hass.config_entries.async_unload(entry.entry_id))
        self.assertTrue(loaded_runtime.stopped)
    async def test_same_publication_retains_sample_timestamp(self):
        self.runtime.start()
        first = self.runtime.snapshot.tin.measured_at
        for _ in range(10):
            self.runtime.refresh()
            self.assertEqual(self.runtime.snapshot.tin.measured_at, first)
