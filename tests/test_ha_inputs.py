"""HA-like source normalization without importing Home Assistant."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from custom_components.fourvrs_server_room.configuration import validate, ConfigurationError
from custom_components.fourvrs_server_room.ha_inputs import read_sample, power_state
from custom_components.fourvrs_server_room.core.models import Power, Quality, Sample

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def state(value, unit="°C", **attributes):
    return SimpleNamespace(state=value, attributes={"unit_of_measurement": unit, **attributes},
                           last_reported=NOW - timedelta(seconds=5))


def settings(**changes):
    return {"trm_temperature": "sensor.trm", "outdoor_temperature": "sensor.outdoor",
            "max_age": 120, "backup_max_skew": 10, "sample_tolerance": 5,
            "recovery_max_gap": 70, "reported_freshness_confirmed": True, **changes}


class NormalizationTests(unittest.TestCase):
    def read(self, value, kind="temperature"):
        return read_sample(value, source="sensor.test", now=100, wall_now=NOW, kind=kind)

    def test_fahrenheit_and_kelvin(self):
        self.assertAlmostEqual(self.read(state("77", "°F")).value, 25)
        self.assertAlmostEqual(self.read(state("298.15", "K")).value, 25)
        self.assertEqual(self.read(state("25")).measured_at, 95)

    def test_restored_and_missing_not_usable(self):
        self.assertEqual(self.read(state("25", restored=True)).quality, Quality.UNVERIFIED)
        self.assertEqual(self.read(None).quality, Quality.UNAVAILABLE)
        self.assertEqual(self.read(state("unknown")).quality, Quality.UNKNOWN)

    def test_bad_unit_nonfinite_and_bad_bool(self):
        for value in (state("nan"), state("25", "watts")):
            self.assertEqual(self.read(value).quality, Quality.INVALID)
        self.assertEqual(self.read(state("false"), "bool").quality, Quality.INVALID)
        self.assertIs(self.read(state("0.0"), "bool").value, False)

    def test_on_battery_has_priority_over_online(self):
        self.assertEqual(power_state(Sample(True, "online", 0), Sample(True, "battery", 0), 0, 60), Power.BATTERY)
        self.assertEqual(power_state(Sample(True, "online", 0), Sample(None, "battery", 0), 0, 60), Power.UNKNOWN)


class ConfigurationTests(unittest.TestCase):
    def test_ages_require_explicit_settings(self):
        data = settings()
        del data["max_age"]
        with self.assertRaises(ConfigurationError):
            validate(data)

    def test_backup_bindings_are_independently_optional(self):
        for changes in ({}, {"bolid_temperature_1": "sensor.b1"},
                        {"bolid_temperature_2": "sensor.b2"},
                        {"bolid_temperature_1": "sensor.b1", "bolid_temperature_2": "sensor.b2"}):
            result = validate(settings(**changes))
            for key, value in changes.items():
                self.assertEqual(result[key], value)

    def test_unverified_freshness_and_duplicate_roles_rejected(self):
        for data in (settings(reported_freshness_confirmed=False),
                     settings(outdoor_temperature="sensor.trm"),
                     settings(critical_on=25)):
            with self.assertRaises(ConfigurationError):
                validate(data)

    def test_domain_validation(self):
        with self.assertRaises(ConfigurationError):
            validate(settings(trm_temperature="switch.fan"))

    def test_fan_output_accepts_readback_switch(self):
        for entity_id in ("switch.fan", "binary_sensor.fan"):
            self.assertEqual(validate(settings(vent_output=entity_id))["vent_output"], entity_id)

    def test_execution_profile_requires_writable_sources_and_contract(self):
        control = dict(control_profile_enabled=True, equipment_readback_confirmed=True,
                       climate="climate.ac", vent_enable="number.cdr", vent_output="switch.fan",
                       ups_online="binary_sensor.online", ups_on_battery="binary_sensor.battery",
                       ac_target=22, emergency_on=24, emergency_off=20, emergency_delta=0)
        self.assertEqual(validate(settings(**control))["command_timeout"], 30)
        for changes in ({"vent_enable": "sensor.cdr"}, {"vent_output": "binary_sensor.fan"},
                        {"equipment_readback_confirmed": False}, {"command_timeout": 0},
                        {"ac_target": float("nan")}, {"emergency_off": 25}):
            with self.subTest(changes=changes), self.assertRaises(ConfigurationError):
                validate(settings(**(control | changes)))
