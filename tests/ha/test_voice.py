"""SRCC/public-service boundary with a fictitious v1 receiver, not Home Control."""
import asyncio
from copy import deepcopy
from unittest.mock import patch
import unittest

from homeassistant.core import SupportsResponse

import test_execution as fixtures
from custom_components.fourvrs_server_room.core.transitions import Step
from custom_components.fourvrs_server_room.configuration import validate, ConfigurationError
from custom_components.fourvrs_server_room.voice import (
    ProcessVoice, REGISTER, PUBLISH, UNREGISTER, READY, LEASE)


class Receiver:
    def __init__(self, hass):
        self.hass = hass
        self.epoch = "center-one"
        self.sources = {}
        self.retired = {}
        self.calls = []
        self.events = []
        self.now = 0
        self.reject = False
        self.hang = False
        self.version = 1

    def start(self):
        for service in (REGISTER, PUBLISH, UNREGISTER):
            self.hass.services.async_register("home_control", service, self.handle,
                                             supports_response=SupportsResponse.ONLY)

    async def handle(self, call):
        data = deepcopy(call.data)
        self.calls.append((call.service, data))
        if self.hang:
            await asyncio.Event().wait()
        if self.reject:
            return dict(accepted=False, reason="rejected")
        source = tuple(data["source"][key] for key in ("domain", "config_entry_id", "process_id"))
        old = self.sources.get(source)
        session, revision = data["producer_session"], data["revision"]
        if session in self.retired.get(source, set()):
            return dict(accepted=False, reason="retired_session")
        if call.service != REGISTER and (data["center_session"] != self.epoch or not old
                                        or old["producer_session"] != session or old["expires"] <= self.now):
            return dict(accepted=False, reason="registration_required")
        if old and old["producer_session"] == session and revision <= old["revision"]:
            return dict(accepted=True, protocol_version=self.version, center_session=self.epoch, lease_seconds=LEASE)
        if call.service == REGISTER:
            if old and old["producer_session"] != session:
                self.retired.setdefault(source, set()).add(old["producer_session"])
            self.sources[source] = dict(data, expires=self.now+LEASE)
        elif call.service == UNREGISTER:
            self.sources.pop(source, None)
            self.retired.setdefault(source, set()).add(session)
        else:
            old["revision"] = revision
            event = data["event"]
            self.events.append(event)
            issues = {item["key"]: item for item in old["active_issues"]}
            if event["resolved"]:
                issues.pop(event["key"], None)
            elif event["active"]:
                issues[event["key"]] = {key: event[key] for key in ("key", "level", "message")}
            old["active_issues"] = list(issues.values())
        return dict(accepted=True, protocol_version=self.version, center_session=self.epoch, lease_seconds=LEASE)


class VoiceTests(unittest.IsolatedAsyncioTestCase):
    service = fixtures.ExecutionTests.service
    settle = fixtures.ExecutionTests.settle
    names = fixtures.ExecutionTests.names
    asyncTearDown = fixtures.ExecutionTests.asyncTearDown

    async def asyncSetUp(self):
        await fixtures.ExecutionTests.asyncSetUp(self)
        self.hass.states.async_set("number.cdr", "0")
        self.hass.states.async_set("switch.fan", "off")
        self.hass.states.async_set("climate.ac", "cool", self.hvac)
        self.runtime.executor.step = Step("COMPLETE", complete=True)
        await self.runtime.voice.async_restore()
        self.runtime.refresh()
        await self.settle()
        self.receiver = Receiver(self.hass)

    async def connect(self):
        self.receiver.start()
        self.hass.bus.async_fire(READY, dict(protocol_version=1, center_session=self.receiver.epoch))
        await self.settle()

    async def state(self, entity, value, attributes=None):
        self.hass.states.async_set(entity, value, attributes)
        await self.settle()

    async def test_defaults_identity_and_center_absence_do_not_block_equipment(self):
        self.runtime.executor.enable(); await self.settle()
        self.assertTrue(self.runtime.executor.step.complete)
        self.assertEqual(self.runtime.voice.connection, "center_unavailable")
        await self.connect()
        registration = self.receiver.calls[0][1]
        self.assertEqual(registration["source"], dict(domain="fourvrs_server_room", config_entry_id="test", process_id="climate"))
        self.assertEqual(set(registration["preferences"].values()), {False})
        self.assertNotIn("climate.ac", str(registration))
        self.assertFalse(self.receiver.events)

    async def test_loaded_center_is_detected_without_ready_and_renew_is_snapshot_only(self):
        self.receiver.start()
        self.runtime.voice.observe(); await self.settle()
        self.assertTrue(self.runtime.voice.registered)
        await self.state("sensor.tin", "29", {"unit_of_measurement": "°C"})
        count = len(self.receiver.events)
        self.runtime.voice._renew(None); await self.settle()
        self.assertEqual(len(self.receiver.events), count)
        self.assertEqual(self.receiver.calls[-1][0], REGISTER)
        self.assertIn("temperature_high", [x["key"] for x in self.receiver.calls[-1][1]["active_issues"]])

    async def test_issue_changes_and_explicit_resolved_without_per_poll_speech(self):
        await self.connect()
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        self.assertEqual(self.receiver.events[-1]["key"], "temperature_high")
        self.assertTrue(self.receiver.events[-1]["active"])
        count = len(self.receiver.calls)
        for _ in range(10):
            self.runtime.refresh()
        await self.settle()
        self.assertEqual(len(self.receiver.calls), count)
        await self.state("sensor.tin", "25", {"unit_of_measurement": "°C"})
        self.assertTrue(self.receiver.events[-1]["resolved"])
        self.assertFalse(any(x["level"] == "INFO" for x in self.receiver.events))

    async def test_unknown_temperature_does_not_resolve_existing_high_temperature(self):
        await self.connect()
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        await self.state("sensor.tin", "unavailable")
        self.assertIn("temperature_high", self.runtime.voice.current)
        self.assertFalse(any(x["key"] == "temperature_high" and x["resolved"] for x in self.receiver.events))

    async def test_preferences_and_persisted_maintenance_only_affect_notifications(self):
        self.runtime.data.update(voice_info=True, voice_warning=True, voice_error=True, voice_repeat=True)
        await self.connect()
        self.runtime.executor.enable(); await self.settle()
        self.assertTrue(self.runtime.executor.enabled)
        await self.state("sensor.tin", "29", {"unit_of_measurement": "°C"})
        calls = list(self.calls)
        enabled = self.runtime.executor.enabled
        await self.runtime.voice.async_set_maintenance(True); await self.settle()
        registered = self.receiver.calls[-1][1]
        self.assertTrue(registered["maintenance"])
        self.assertEqual(registered["active_issues"], [])
        self.assertEqual(self.calls, calls)
        self.assertEqual(self.runtime.executor.enabled, enabled)
        self.runtime.voice.stop(); await self.settle()
        self.runtime.voice = ProcessVoice(self.runtime)
        await self.runtime.voice.async_restore()
        self.assertTrue(self.runtime.voice.maintenance)
        self.runtime.voice.start(); await self.settle()
        self.assertFalse(self.runtime.voice.infos)
        await self.runtime.voice.async_set_maintenance(False); await self.settle()
        self.assertIn("temperature_high", [x["key"] for x in self.receiver.calls[-1][1]["active_issues"]])
        self.assertFalse(self.runtime.voice.infos)

    async def test_restart_center_reregisters_current_snapshot_without_old_info(self):
        self.runtime.data["voice_info"] = True
        await self.connect()
        await self.state("sensor.tin", "29", {"unit_of_measurement": "°C"})
        self.receiver.sources.clear()
        self.receiver.epoch = "center-two"
        self.hass.bus.async_fire(READY, dict(protocol_version=1, center_session="center-two"))
        await self.settle()
        self.assertEqual(self.receiver.calls[-1][0], REGISTER)
        self.assertEqual(self.runtime.voice.center_session, "center-two")
        self.assertTrue(self.receiver.calls[-1][1]["active_issues"])
        self.assertFalse(any(x["level"] == "INFO" for x in self.receiver.events))

    async def test_rejection_and_timeout_backoff_are_isolated_from_executor(self):
        await self.connect()
        self.receiver.reject = True
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        self.assertFalse(self.runtime.voice.registered)
        self.assertIsNone(self.runtime.executor.error)
        count = len(self.receiver.calls)
        for _ in range(10):
            self.runtime.refresh()
        await self.settle()
        self.assertEqual(len(self.receiver.calls), count)
        self.receiver.reject = False
        self.receiver.hang = True
        self.runtime.voice.retry_at = 0
        with patch("custom_components.fourvrs_server_room.voice.CALL_TIMEOUT", .01):
            self.runtime.voice._renew(None)
            await self.settle()
        self.assertEqual(self.runtime.voice.connection, "center_error")
        self.assertIsNone(self.runtime.executor.error)

    async def test_version_mismatch_does_not_publish(self):
        self.receiver.version = 2
        await self.connect()
        self.assertFalse(self.runtime.voice.registered)
        self.assertEqual(self.receiver.events, [])

    async def test_stop_unregisters_and_old_session_cannot_remove_new_source(self):
        await self.connect()
        old = self.runtime.voice
        old.stop(); await self.settle()
        self.assertEqual(self.receiver.calls[-1][0], UNREGISTER)
        self.runtime.voice = ProcessVoice(self.runtime)
        await self.runtime.voice.async_restore()
        self.runtime.voice.start(); await self.settle()
        new_registration = self.receiver.calls[-1][1]
        self.assertNotEqual(new_registration["producer_session"], old.session)
        reply = await self.hass.services.async_call("home_control", UNREGISTER,
            dict(old._envelope(), center_session=self.receiver.epoch), blocking=True, return_response=True)
        self.assertFalse(reply["accepted"])
        self.assertTrue(self.receiver.sources)

    async def test_expired_lease_recovers_with_current_snapshot(self):
        await self.connect()
        registration = deepcopy(self.receiver.calls[-1][1])
        session = registration["producer_session"]
        self.receiver.now = 181
        await self.hass.services.async_call("home_control", REGISTER, registration,
                                            blocking=True, return_response=True)
        self.assertEqual(next(iter(self.receiver.sources.values()))["expires"], 180)
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        self.assertFalse(self.runtime.voice.registered)
        self.runtime.voice.retry_at = 0
        self.runtime.voice._renew(None); await self.settle()
        self.assertTrue(self.runtime.voice.registered)
        self.assertEqual(self.receiver.calls[-1][1]["producer_session"], session)
        self.assertGreater(self.receiver.calls[-1][1]["revision"], registration["revision"])
        self.assertIn("temperature_high", [x["key"] for x in self.receiver.calls[-1][1]["active_issues"]])

    async def test_voice_preferences_are_strict_booleans(self):
        for value in ("true", 1, None):
            with self.assertRaises(ConfigurationError):
                validate(dict(fixtures.settings(), voice_error=value))

    async def test_equipment_replacement_keeps_source_identity(self):
        await self.connect()
        source = self.receiver.calls[-1][1]["source"]
        self.runtime.data["climate"] = "climate.replacement"
        self.runtime.refresh(); await self.settle()
        self.runtime.voice._renew(None); await self.settle()
        self.assertEqual(self.receiver.calls[-1][1]["source"], source)

    async def test_info_only_after_completed_command_not_intermediate_state(self):
        self.runtime.data["voice_info"] = True
        await self.connect()
        self.assertEqual(self.runtime.voice.projection.mode, "air_conditioning")
        await self.state("climate.ac", "off", self.hvac)
        self.assertEqual(self.runtime.voice.projection.mode, "air_conditioning")
        self.assertFalse(any(x["level"] == "INFO" for x in self.receiver.events))
        self.runtime.executor.last_command = dict(sequence=100, action="ac_off", status="confirmed")
        self.runtime.notify(); await self.settle()
        modes = [x for x in self.receiver.events if x["key"] == "mode_changed"]
        self.assertEqual(len(modes), 1, (self.runtime.reported_mode, self.runtime.executor.step, self.runtime.voice.diagnostics(), self.runtime.voice.projection.mode, self.receiver.calls[-3:], self.runtime.voice.infos))
        self.runtime.notify(); await self.settle()
        self.assertEqual(len([x for x in self.receiver.events if x["key"] == "mode_changed"]), 1)

    async def test_recovery_info_and_no_replay_after_disconnect(self):
        self.runtime.data["voice_info"] = True
        await self.connect()
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        await self.state("sensor.tin", "25", {"unit_of_measurement": "°C"})
        self.assertEqual(self.receiver.events[-1]["key"], "temperature_high")
        self.assertEqual(self.receiver.events[-1]["level"], "INFO")
        count = len(self.receiver.events)
        self.hass.services.async_remove("home_control", PUBLISH)
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        await self.state("sensor.tin", "25", {"unit_of_measurement": "°C"})
        self.receiver.start()
        self.runtime.voice._renew(None); await self.settle()
        self.assertEqual(len(self.receiver.events), count)

    async def test_receiver_duplicate_and_stale_revision_have_no_side_effect(self):
        await self.connect()
        registration = self.receiver.calls[-1][1]
        await self.state("sensor.tin", "26", {"unit_of_measurement": "°C"})
        published = deepcopy(self.receiver.calls[-1][1])
        count = len(self.receiver.events)
        for service, body in ((PUBLISH, published), (REGISTER, registration)):
            reply = await self.hass.services.async_call("home_control", service, body,
                                                        blocking=True, return_response=True)
            self.assertTrue(reply["accepted"])
        self.assertEqual(len(self.receiver.events), count)
        record = next(iter(self.receiver.sources.values()))
        self.assertIn("temperature_high", [x["key"] for x in record["active_issues"]])

    async def test_area_and_name_metadata_do_not_change_source(self):
        self.runtime.data["voice_area"] = "server_room"
        self.runtime.entry.title = "Cooling process"
        await self.connect()
        registration = self.receiver.calls[-1][1]
        self.assertEqual(registration["area_id"], "server_room")
        source = registration["source"]
        self.runtime.data["voice_area"] = "utility_room"
        self.runtime.entry.title = "Renamed process"
        self.runtime.notify(); await self.settle()
        self.assertEqual(self.receiver.calls[-1][1]["source"], source)
        self.assertEqual(self.receiver.calls[-1][1]["name"], "Renamed process")
        self.assertEqual(self.receiver.calls[-1][1]["area_id"], "utility_room")
