"""End-to-end observations, including degradation of the reserved Bolid pair."""
import unittest
from dataclasses import replace

from custom_components.fourvrs_server_room.core.controller import Observer
from custom_components.fourvrs_server_room.core.models import Config, Mode, Quality, Severity
from custom_components.fourvrs_server_room.core.inputs import Fallback, select_temperature
from test_core import snapshot, temp


class BackupTests(unittest.TestCase):
    def observer(self):
        return Observer(Config(), max_age=60, recovery_max_gap=60,
                        sample_tolerance=0, backup_max_skew=5)

    def failed_primary(self, now=0):
        return snapshot(now, tin=temp(None, now, quality=Quality.UNAVAILABLE))

    def test_single_value_no_average_and_warning(self):
        result = self.observer().evaluate(self.failed_primary(), backups=(
            temp(25, source="b1"), temp(None, source="b2", quality=Quality.UNAVAILABLE)))
        self.assertEqual(result.temperature.sample.value, 25)
        self.assertEqual(result.temperature.sample.source, "b1")
        self.assertEqual(result.alarm.severity, Severity.WARNING)
        self.assertIn("BACKUP_UNAVAILABLE:b2", result.warnings)
        self.assertTrue(result.degraded)

    def test_single_critical_is_not_downgraded_to_warning(self):
        result = self.observer().evaluate(self.failed_primary(), backups=(
            temp(28, source="b1"), temp(None, source="b2")))
        self.assertEqual(result.alarm.severity, Severity.CRITICAL)
        self.assertTrue(result.decision.urgent)
        self.assertFalse(result.trend.ready)

    def test_both_missing_no_fabricated_temperature(self):
        result = self.observer().evaluate(self.failed_primary(), backups=(
            temp(None, source="b1"), temp(None, source="b2")))
        self.assertIsNone(result.temperature.sample)
        self.assertIsNone(result.decision.desired)
        self.assertEqual(len(result.warnings), 2)

    def test_backup_not_used_with_good_primary(self):
        result = self.observer().evaluate(snapshot(), backups=(
            temp(30, source="b1"), temp(None, source="b2")))
        self.assertEqual(result.temperature.sample.source, "trm")
        self.assertEqual(result.alarm.severity, Severity.NORMAL)

    def test_mean_single_mean_resets_source_window(self):
        observer = self.observer()
        state = replace(self.failed_primary(), vent_enabled=temp(True, source="cdr"),
                        vent_output=temp(True, source="fan"))
        observer.evaluate(state, backups=(temp(24, source="b1"), temp(26, source="b2")))
        state = replace(self.failed_primary(60), vent_enabled=temp(True, 60, "cdr"),
                        vent_output=temp(True, 60, "fan"))
        result = observer.evaluate(state, backups=(temp(25, 60, "b1"), temp(None, 60, "b2")))
        self.assertEqual(result.trend.samples, 0)
        self.assertEqual(observer.trend.started, 60)
        state = replace(self.failed_primary(120), vent_enabled=temp(True, 120, "cdr"),
                        vent_output=temp(True, 120, "fan"))
        result = observer.evaluate(state, backups=(temp(24, 120, "b1"), temp(26, 120, "b2")))
        self.assertEqual(result.trend.samples, 0)
        self.assertEqual(result.warnings, ())
        self.assertEqual(result.temperature.sample.value, 25)

    def test_stale_backup_uses_valid_one(self):
        result = select_temperature(temp(None), (temp(23, 0, "b1"), temp(25, 61, "b2")),
                                    now=61, max_age=60, fallback=Fallback.MEAN_ALLOW_ONE,
                                    max_skew=5)
        self.assertEqual(result.sample.value, 25)
        self.assertEqual(result.warning_sources, ("b1",))

    def test_initial_dead_band_prefers_ventilation(self):
        state = snapshot(tout=temp(0.5), current=None)
        result = self.observer().evaluate(state)
        self.assertEqual(result.decision.desired, Mode.VENTILATION)
        self.assertEqual(result.decision.reason, "INITIAL_VENT_SELECTED")


if __name__ == "__main__":
    unittest.main()


class OptionalBackupTests(unittest.TestCase):
    def test_zero_or_one_configured_backup(self):
        primary = temp(None, quality=Quality.UNAVAILABLE)
        kwargs = dict(now=0, max_age=60, fallback=Fallback.MEAN_ALLOW_ONE, max_skew=5)
        missing = select_temperature(primary, (), **kwargs)
        self.assertIsNone(missing.sample)
        self.assertEqual(missing.reason, "FALLBACK_NOT_CONFIGURED")
        self.assertEqual(missing.warning_sources, ())
        single = select_temperature(primary, (temp(27, source="backup"),), **kwargs)
        self.assertEqual(single.sample.value, 27)
        self.assertEqual(single.warning_sources, ())
        self.assertTrue(single.degraded)  # Primary failed, not an absent second sensor.
        failed = select_temperature(primary, (temp(None, source="backup"),), **kwargs)
        self.assertIsNone(failed.sample)
        self.assertEqual(failed.warning_sources, ("backup",))
