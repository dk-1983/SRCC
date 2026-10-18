"""Thermal reserve is independent of seasonal preference and trend setup."""
import unittest
from dataclasses import replace

from test_core import snapshot, temp
from custom_components.fourvrs_server_room.core.controller import Observer
from custom_components.fourvrs_server_room.core.models import Config, Mode, Power
from custom_components.fourvrs_server_room.core.policy import choose
from custom_components.fourvrs_server_room.core.transitions import Action, TransitionPlanner


class ThermalReserveTests(unittest.TestCase):
    def test_warning_and_rising_ventilation_locks_retry_before_critical(self):
        observer = Observer(Config(), max_age=70, recovery_max_gap=70,
                            sample_tolerance=5, backup_max_skew=5)
        for now in (0, 60, 120, 180, 240, 300):
            result = observer.evaluate(snapshot(now, tin=temp(25.5 + now/300, now),
                tout=temp(-4, now), vent_enabled=temp(True, now), vent_output=temp(True, now)))
        self.assertFalse(result.alarm.thermal_critical)
        self.assertEqual(result.decision.reason, "VENT_RETRY_LOCKED")
        self.assertTrue(result.decision.urgent)
        result = observer.evaluate(snapshot(301, tin=temp(22, 301), tout=temp(-4, 301)))
        self.assertEqual(result.decision.desired, Mode.AIR_CONDITIONING)
        result = observer.evaluate(snapshot(302, tin=temp(25, 302), tout=temp(-4, 302), ac_available=False))
        self.assertEqual(result.decision.desired, Mode.VENTILATION)

    def test_rack_offset_is_in_degrees_and_inlet_is_irrelevant(self):
        from custom_components.fourvrs_server_room.core.severity import TemperatureAlarm
        alarm = TemperatureAlarm(Config(rack_recovery_offset=2), max_age=60, max_gap=60)
        alarm.update(temp(28), now=0, ac_target=22)
        self.assertTrue(alarm.update(temp(24.1, 1), now=1, ac_target=22,
                                    ac_temperature=temp(16.5, 1)).thermal_critical)
        self.assertFalse(alarm.update(temp(24, 2), now=2, ac_target=22,
                                     ac_temperature=None).thermal_critical)

    def test_negative_seasonal_thresholds_and_inclusive_hard_limit(self):
        config = Config(ac_hard_min=-15, ac_to_vent=-4, vent_to_ac=-2)
        for current, outside, expected in (
            (Mode.VENTILATION, -2, Mode.AIR_CONDITIONING),
            (Mode.VENTILATION, -2.01, Mode.VENTILATION),
            (Mode.AIR_CONDITIONING, -4, Mode.VENTILATION),
            (Mode.AIR_CONDITIONING, -3.99, Mode.AIR_CONDITIONING),
        ):
            decision = choose(snapshot(tout=temp(outside), current=current), config, max_age=60)
            self.assertEqual(decision.desired, expected)
        for outside, expected in ((-15, Mode.AIR_CONDITIONING), (-15.01, Mode.NO_COOLING_AVAILABLE)):
            decision = choose(snapshot(tin=temp(28), tout=temp(outside), vent_ineffective=True), config, max_age=60)
            self.assertEqual(decision.desired, expected)
        step = TransitionPlanner(config, max_age=60, timeout=30, ac_target=22,
                                 live_target_updates=True).advance(
            snapshot(tout=temp(-15)), Mode.AIR_CONDITIONING,
            automatic=True, ready=True, timing_allowed=True)
        self.assertEqual(step.action, Action.AC_COOL)

    def test_critical_selects_ac_without_trend_and_holds_until_recovery(self):
        observer = Observer(Config(), max_age=70, recovery_max_gap=70,
                            sample_tolerance=5, backup_max_skew=5)
        for now, value in ((0, 28), (60, 27.5), (120, 27), (180, 26.8)):
            result = observer.evaluate(snapshot(now, tin=temp(value, now), tout=temp(-1, now)))
            self.assertEqual(result.decision.desired, Mode.AIR_CONDITIONING)
            self.assertEqual(result.decision.reason, "THERMAL_RESERVE_AC")
            self.assertTrue(result.decision.urgent)
        result = observer.evaluate(snapshot(240, tin=temp(22, 240), tout=temp(-1, 240), ac_temperature=temp(22, 240)))
        self.assertFalse(result.alarm.thermal_critical)
        self.assertEqual(result.decision.desired, Mode.AIR_CONDITIONING)
        self.assertEqual(result.decision.reason, "VENT_RETRY_LOCKED")

    def test_unavailable_or_cold_ac_does_not_discard_working_ventilation(self):
        for changes in ({"ac_available": False}, {"ac_fault": True}, {"tout": temp(-16)}):
            state = snapshot(tin=temp(28), tout=temp(-1))
            decision = choose(replace(state, **changes), Config(), max_age=60)
            self.assertEqual(decision.desired, Mode.VENTILATION)
        decision = choose(snapshot(tin=temp(28), tout=temp(-1), power=Power.UNKNOWN), Config(), max_age=60)
        self.assertIsNone(decision.desired)

    def test_reserve_launch_below_zero_is_explicit_and_hard_minimum_remains(self):
        def run(state):
            return TransitionPlanner(Config(), max_age=60, timeout=30, ac_target=22,
                                     live_target_updates=True).advance(
                state, Mode.AIR_CONDITIONING, automatic=True, ready=True, timing_allowed=True)
        state = snapshot(tin=temp(27.5), tout=temp(-1), thermal_critical=True)
        self.assertEqual(run(state).action, Action.AC_COOL)
        self.assertEqual(run(replace(state, thermal_critical=False)).action, Action.AC_COOL)
        self.assertEqual(run(replace(state, tout=temp(-16))).reason, "AC_HARD_MIN_BLOCK")

    def test_running_ac_is_not_stopped_for_seasonal_vent_during_critical(self):
        decision = choose(snapshot(tin=temp(28), tout=temp(-1), current=Mode.AIR_CONDITIONING),
                          Config(), max_age=60)
        self.assertEqual(decision.desired, Mode.AIR_CONDITIONING)
