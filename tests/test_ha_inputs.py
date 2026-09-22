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

    def test_optional_backup_bindings_require_pair(self):
        with self.assertRaises(ConfigurationError):
            validate(settings(bolid_temperature_1="sensor.b1"))
        self.assertIn("bolid_temperature_2", validate(settings(
            bolid_temperature_1="sensor.b1", bolid_temperature_2="sensor.b2")))

    def test_unverified_freshness_and_duplicate_roles_rejected(self):
        for data in (settings(reported_freshness_confirmed=False),
                     settings(outdoor_temperature="sensor.trm"),
                     settings(critical_on=25)):
            with self.assertRaises(ConfigurationError):
                validate(data)

    def test_domain_validation(self):
        with self.assertRaises(ConfigurationError):
            validate(settings(trm_temperature="switch.fan"))
