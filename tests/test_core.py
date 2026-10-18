"""Safety and state-machine scenarios with controlled timestamps."""
import unittest
from dataclasses import replace

from custom_components.fourvrs_server_room.core.models import (
    Config, Mode, Power, Quality, Sample, Snapshot, Severity,
)
from custom_components.fourvrs_server_room.core.inputs import Fallback, select_temperature
from custom_components.fourvrs_server_room.core.health import VentilationTrend
from custom_components.fourvrs_server_room.core.severity import TemperatureAlarm
from custom_components.fourvrs_server_room.core.policy import choose
from custom_components.fourvrs_server_room.core.transitions import Action, TransitionPlanner


def temp(value, at=0, source="trm", quality=Quality.VALID):
    return Sample(value, source, at, quality)


def snapshot(now=0, **changes):
    result = Snapshot(now, temp(25.0, now), temp(2.0, now, "outdoor"),
                      Power.MAINS, True, True,
                      Sample(False, "cdr", now), Sample(False, "fan", now),
                      Sample("off", "ac", now), temp(22.0, now, "target"),
                      ac_fault=False, current=Mode.VENTILATION)
    return replace(result, **changes)


class InputTests(unittest.TestCase):
    def test_primary_wins_over_hot_backup(self):
        result = select_temperature(temp(25), (temp(29, source="b1"), temp(29, source="b2")),
                                    now=0, max_age=60, fallback=Fallback.MEAN_REQUIRE_BOTH,
                                    max_skew=5)
        self.assertEqual(result.sample.value, 25)
        self.assertFalse(result.degraded)

    def test_both_required_and_average(self):
        bad = temp(None, quality=Quality.UNAVAILABLE)
        backups = (temp(27, source="b1"), temp(29, source="b2"))
        result = select_temperature(bad, backups, now=0, max_age=60,
                                    fallback=Fallback.MEAN_REQUIRE_BOTH, max_skew=5)
        self.assertEqual(result.sample.value, 28)
        self.assertTrue(result.degraded)
        for index in (0, 1):
            values = list(backups)
            values[index] = replace(values[index], quality=Quality.STALE)
            self.assertIsNone(select_temperature(bad, tuple(values), now=0, max_age=60,
                              fallback=Fallback.MEAN_REQUIRE_BOTH, max_skew=5).sample)

    def test_mean_keeps_oldest_timestamp_and_stable_source(self):
        backups = (temp(27, 10, "b1"), temp(29, 15, "b2"))
        kwargs = dict(now=15, max_age=60, fallback=Fallback.MEAN_REQUIRE_BOTH, max_skew=5)
        result = select_temperature(temp(None), backups, **kwargs)
        reverse = select_temperature(temp(None), tuple(reversed(backups)), **kwargs)
        self.assertEqual(result.sample.measured_at, 10)
        self.assertEqual(result.sample, reverse.sample)
        self.assertIsNone(select_temperature(temp(None), backups, **(kwargs | {"max_skew": 4})).sample)

    def test_invalid_values_and_future_samples(self):
        for value in (float("nan"), float("inf"), True, "24", None):
            self.assertIsNone(temp(value).temperature(0, 60))
        self.assertIsNone(temp(25, 1).temperature(0, 60))
        self.assertIsNone(temp(25).temperature(61, 60))
        self.assertIsNone(temp(25, quality=Quality.UNVERIFIED).temperature(0, 60))

    def test_duplicate_backup_entities_rejected(self):
        result = select_temperature(temp(None), (temp(27, source="b"), temp(28, source="b")),
                                    now=0, max_age=60, fallback=Fallback.MEAN_REQUIRE_BOTH,
                                    max_skew=5)
        self.assertIsNone(result.sample)


class AlarmTests(unittest.TestCase):
    def alarm(self):
        return TemperatureAlarm(Config(), max_age=60, max_gap=60)

    def test_critical_at_equality_and_warning(self):
        alarm = self.alarm()
        self.assertEqual(alarm.update(temp(26), now=0).severity, Severity.WARNING)
        self.assertEqual(alarm.update(temp(28), now=0).severity, Severity.CRITICAL)

    def test_elapsed_time_alone_never_recovers(self):
        alarm = self.alarm()
        alarm.update(temp(28), now=0)
        for now in (60, 120, 600):
            self.assertTrue(alarm.update(temp(26, now), now=now,
                            ac_temperature=temp(23, now), ac_target=22).thermal_critical)

    def test_measured_target_recovers_without_timer(self):
        alarm = self.alarm()
        alarm.update(temp(28), now=0)
        result = alarm.update(temp(22, 1), now=1, ac_temperature=temp(30, 1), ac_target=22)
        self.assertFalse(result.thermal_critical)

    def test_cool_ac_does_not_hide_hot_rack(self):
        alarm = self.alarm()
        alarm.update(temp(28), now=0)
        self.assertTrue(alarm.update(temp(27.5, 1), now=1,
                        ac_temperature=temp(21, 1), ac_target=22).thermal_critical)

    def test_ac_temperature_cannot_clear_a_warm_rack(self):
        for sample in (None, temp(22, 0), temp(None, 100)):
            alarm = self.alarm()
            alarm.update(temp(28, 1), now=1)
            self.assertTrue(alarm.update(temp(26, 100), now=100,
                            ac_temperature=sample, ac_target=22).thermal_critical)

    def test_missing_rack_does_not_recover(self):
        alarm = self.alarm()
        alarm.update(temp(28), now=0)
        self.assertTrue(alarm.update(temp(None, 1), now=1,
                        ac_temperature=temp(22, 1), ac_target=22).thermal_critical)

    def test_other_critical_survives_temperature_recovery(self):
        alarm = self.alarm()
        alarm.update(temp(28), now=0)
        result = alarm.update(temp(22, 1), now=1, other_critical=True,
                              ac_temperature=temp(22, 1), ac_target=22)
        self.assertFalse(result.thermal_critical)
        self.assertEqual(result.severity, Severity.CRITICAL)


class TrendTests(unittest.TestCase):
    def trend(self, **config):
        return VentilationTrend(Config(**config), max_age=60, tolerance=0)

    def test_first_result_at_five_minutes(self):
        trend = self.trend(minimum_vent_rise=0.5)
        trend.update(temp(24), now=0, active=True)
        for now in (60, 120, 180, 240):
            self.assertFalse(trend.update(temp(24 + now / 300, now), now=now, active=True).ready)
        result = trend.update(temp(25, 300), now=300, active=True)
        self.assertTrue(result.ready)
        self.assertEqual(result.samples, 5)
        self.assertAlmostEqual(result.rate_per_minute, 0.2)
        self.assertTrue(result.ineffective)

    def test_unset_rise_threshold_is_not_failure(self):
        trend = self.trend()
        for now in (0, 60, 120, 180, 240, 300):
            result = trend.update(temp(24 + now / 300, now), now=now, active=True)
        self.assertTrue(result.ready)
        self.assertIsNone(result.ineffective)

    def test_off_or_duplicate_resets_window(self):
        trend = self.trend()
        trend.update(temp(24), now=0, active=True)
        result = trend.update(temp(24), now=60, active=True)
        self.assertEqual(result.reason, "MISSED_OR_DUPLICATE_SAMPLE")
        trend.update(temp(24, 120), now=120, active=True)
        self.assertFalse(trend.update(temp(24, 180), now=180, active=False).ready)
        self.assertIsNone(trend.started)

    def test_unchanged_new_measurements_are_valid(self):
        trend = self.trend(minimum_vent_rise=0.5)
        for now in range(0, 301, 60):
            result = trend.update(temp(24, now), now=now, active=True)
        self.assertTrue(result.ready)
        self.assertFalse(result.ineffective)


class PolicyTests(unittest.TestCase):
    def test_hysteresis_boundaries(self):
        for current, outside, desired in (
            (Mode.VENTILATION, 0.5, Mode.VENTILATION),
            (Mode.VENTILATION, 1, Mode.AIR_CONDITIONING),
            (Mode.AIR_CONDITIONING, -0.5, Mode.AIR_CONDITIONING),
            (Mode.AIR_CONDITIONING, -1, Mode.VENTILATION),
        ):
            with self.subTest(current=current, outside=outside):
                result = choose(snapshot(tout=temp(outside), current=current), Config(), max_age=60)
                self.assertEqual(result.desired, desired)

    def test_failed_vent_reserve_and_hard_minimum(self):
        for outside, desired in ((-2, Mode.AIR_CONDITIONING),
                                 (-15, Mode.AIR_CONDITIONING),
                                 (-15.1, Mode.NO_COOLING_AVAILABLE),
                                 (-40, Mode.NO_COOLING_AVAILABLE)):
            result = choose(snapshot(tout=temp(outside), vent_ineffective=True), Config(), max_age=60)
            self.assertEqual(result.desired, desired)

    def test_hot_outdoor_no_emergency_vent(self):
        result = choose(snapshot(tin=temp(29), tout=temp(31), ac_available=False),
                        Config(emergency_delta=5), max_age=60)
        self.assertEqual(result.desired, Mode.IDLE)

    def test_battery_or_unknown_does_not_assume_mains(self):
        for power in (Power.BATTERY, Power.UNKNOWN, Power.CONFLICT):
            result = choose(snapshot(power=power), Config(), max_age=60)
            self.assertIsNone(result.desired)
            self.assertEqual(result.reason, "POWER_POLICY_REQUIRED")

    def test_critical_marks_immediate_evaluation(self):
        result = choose(snapshot(tin=temp(28)), Config(), max_age=60)
        self.assertTrue(result.urgent)


class TransitionTests(unittest.TestCase):
    def planner(self):
        return TransitionPlanner(Config(), max_age=60, timeout=30, ac_target=22)

    def advance(self, planner, state, target=Mode.AIR_CONDITIONING, **kwargs):
        args = dict(automatic=True, ready=True, timing_allowed=True) | kwargs
        return planner.advance(state, target, **args)

    def test_vent_to_ac_readback_order(self):
        planner = self.planner()
        state = snapshot(vent_enabled=Sample(True, "cdr", 0), vent_output=Sample(True, "fan", 0))
        self.assertEqual(self.advance(planner, state).action, Action.VENT_DISABLE)
        self.assertEqual(self.advance(planner, state).phase, "WAIT_READBACK")
        state = snapshot(1, vent_output=Sample(True, "fan", 1))
        self.assertEqual(self.advance(planner, state).phase, "WAIT_VENT_OUTPUT_OFF")
        self.assertEqual(self.advance(planner, snapshot(2)).action, Action.AC_COOL)
        self.assertFalse(self.advance(planner, snapshot(3)).complete)
        self.assertTrue(self.advance(planner, snapshot(4, ac_mode=Sample("cool", "ac", 4))).complete)

    def test_ac_off_before_vent_enable(self):
        planner = self.planner()
        state = snapshot(tout=temp(-2), ac_mode=Sample("cool", "ac", 0))
        self.assertEqual(self.advance(planner, state, Mode.VENTILATION).action, Action.AC_OFF)
        self.assertEqual(self.advance(planner, state, Mode.VENTILATION).phase, "WAIT_READBACK")
        state = snapshot(1, tout=temp(-2, 1))
        self.assertEqual(self.advance(planner, state, Mode.VENTILATION).action, Action.VENT_ENABLE)

    def test_unavailable_ac_is_not_off(self):
        state = snapshot(ac_available=False, ac_mode=Sample(None, "ac", 0, Quality.UNAVAILABLE))
        result = self.advance(self.planner(), state, Mode.VENTILATION)
        self.assertIsNone(result.action)
        self.assertEqual(result.reason, "AC_OFF_UNPROVEN")

    def test_manual_stops_pending_and_requires_reconciliation(self):
        planner = self.planner()
        self.advance(planner, snapshot())
        self.assertEqual(self.advance(planner, snapshot(1), automatic=False).phase, "PAUSED_MANUAL")
        self.assertEqual(self.advance(planner, snapshot(2)).phase, "BLOCKED")

    def test_timeout_no_retry(self):
        planner = self.planner()
        self.advance(planner, snapshot())
        result = self.advance(planner, snapshot(30))
        self.assertEqual(result.reason, "READBACK_TIMEOUT")
        self.assertIsNone(self.advance(planner, snapshot(31)).action)

    def test_guard_rechecked_after_vent_disabled(self):
        planner = self.planner()
        self.advance(planner, snapshot(vent_enabled=Sample(True, "cdr", 0)))
        result = self.advance(planner, snapshot(1, tout=temp(-16, 1)))
        self.assertEqual(result.reason, "AC_HARD_MIN_BLOCK")
        self.assertIsNone(result.action)

    def test_unknown_or_optimistic_vent_is_not_off(self):
        for quality in (Quality.UNKNOWN, Quality.UNVERIFIED, Quality.STALE):
            planner = self.planner()
            result = self.advance(planner, snapshot(vent_enabled=Sample(False, "cdr", 0, quality)))
            self.assertNotEqual(result.action, Action.AC_COOL)

    def test_all_heat_and_auto_modes_require_off(self):
        for mode in ("heat", "auto", "heat_cool", "idle"):
            state = snapshot(ac_mode=Sample(mode, "ac", 0))
            self.assertEqual(self.advance(self.planner(), state, Mode.VENTILATION).action, Action.AC_OFF)

    def test_service_error_latches_block(self):
        planner = self.planner()
        self.advance(planner, snapshot())
        planner.fail("SERVICE_ERROR")
        self.assertEqual(self.advance(planner, snapshot(1)).reason, "SERVICE_ERROR")

    def test_no_start_before_ready_or_timing(self):
        self.assertIsNone(self.advance(self.planner(), snapshot(), ready=False).action)
        self.assertIsNone(self.advance(self.planner(), snapshot(), timing_allowed=False).action)


class ConfigTests(unittest.TestCase):
    def test_invalid_configuration_rejected(self):
        for changes in ({"critical_recovery": 28}, {"ac_hard_min": 0},
                        {"warning_on": 29}, {"minimum_vent_rise": 0},
                        {"critical_on": float("nan")}, {"sample_interval": 0}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Config(**changes)


if __name__ == "__main__":
    unittest.main()


class OptionalAcFaultTests(unittest.TestCase):
    def test_policy_and_planner_distinguish_absent_from_lost_fault_signal(self):
        for monitored, fault, allowed in ((False, None, True), (True, None, False),
                                          (True, True, False), (False, True, False),
                                          (True, False, True)):
            with self.subTest(monitored=monitored, fault=fault):
                state = snapshot(ac_fault_monitored=monitored, ac_fault=fault)
                decision = choose(state, Config(), max_age=60)
                self.assertEqual(decision.desired is Mode.AIR_CONDITIONING, allowed)
                planner = TransitionPlanner(Config(), max_age=60, timeout=30, ac_target=22)
                step = planner.advance(state, Mode.AIR_CONDITIONING, automatic=True,
                                       ready=True, timing_allowed=True)
                self.assertEqual(step.action is Action.AC_COOL, allowed)

    def test_absent_fault_does_not_bypass_ac_availability_or_minimum(self):
        for changes in ({"ac_available": False}, {"tout": temp(-16)}):
            state = snapshot(ac_fault_monitored=False, ac_fault=None, **changes)
            self.assertIsNot(choose(state, Config(), max_age=60).desired, Mode.AIR_CONDITIONING)
            step = TransitionPlanner(Config(), max_age=60, timeout=30, ac_target=22).advance(
                state, Mode.AIR_CONDITIONING, automatic=True, ready=True, timing_allowed=True)
            self.assertIsNot(step.action, Action.AC_COOL)


class ManualVentilationTests(unittest.TestCase):
    def config(self):
        return Config(emergency_on=24, emergency_off=20, emergency_delta=0)

    def test_reserve_outdoor_limit_and_power_evidence(self):
        for outdoor, mode in ((9.9, Mode.EMERGENCY_VENTILATION), (10, Mode.IDLE), (11, Mode.IDLE)):
            state = snapshot(tout=temp(outdoor), power=Power.BATTERY, ac_available=False,
                             ac_mode=Sample(None, "ac", 0, Quality.UNAVAILABLE), ac_unpowered=True)
            self.assertEqual(choose(state, self.config(), max_age=60, cooling_required=True).desired, mode)
        for proof, power, expected in ((True, Power.BATTERY, Action.FAN_ON),
                                       (False, Power.BATTERY, None), (True, Power.MAINS, None)):
            state = snapshot(tout=temp(5), power=power, ac_available=False,
                             ac_mode=Sample(None, "ac", 0, Quality.UNAVAILABLE), ac_unpowered=proof)
            planner = TransitionPlanner(self.config(), max_age=60, timeout=30, ac_target=22, manual_fan=True)
            result = planner.advance(state, Mode.EMERGENCY_VENTILATION, automatic=True, ready=True, timing_allowed=True)
            self.assertEqual(result.action, expected)

    def test_manual_output_off_requires_binding_off(self):
        planner = TransitionPlanner(self.config(), max_age=60, timeout=30, ac_target=22, manual_fan=True)
        args = dict(automatic=True, ready=True, timing_allowed=True)
        state = snapshot(vent_enabled=Sample(True, "cdr", 0), vent_output=Sample(True, "fan", 0))
        self.assertEqual(planner.advance(state, Mode.AIR_CONDITIONING, **args).action, Action.VENT_DISABLE)
        self.assertIsNone(planner.advance(state, Mode.AIR_CONDITIONING, **args).action)
        state = snapshot(1, vent_output=Sample(True, "fan", 1))
        self.assertEqual(planner.advance(state, Mode.AIR_CONDITIONING, **args).action, Action.FAN_OFF)
        self.assertIsNone(planner.advance(state, Mode.AIR_CONDITIONING, **args).action)
        self.assertEqual(planner.advance(snapshot(2), Mode.AIR_CONDITIONING, **args).action, Action.AC_COOL)


class SeasonalSequenceTests(unittest.TestCase):
    def test_temperature_oscillations_keep_mode_until_opposite_boundary(self):
        mode = Mode.VENTILATION
        sequence = [(-2, Mode.VENTILATION), (0.2, Mode.VENTILATION),
                    (0.9, Mode.VENTILATION), (1, Mode.AIR_CONDITIONING),
                    (0.8, Mode.AIR_CONDITIONING), (0, Mode.AIR_CONDITIONING),
                    (-0.9, Mode.AIR_CONDITIONING), (-1, Mode.VENTILATION),
                    (-0.5, Mode.VENTILATION), (0.9, Mode.VENTILATION)]
        for outside, expected in sequence:
            with self.subTest(outside=outside, mode=mode):
                result = choose(snapshot(tout=temp(outside), current=mode), Config(), max_age=60)
                self.assertEqual(result.desired, expected)
                mode = result.desired
