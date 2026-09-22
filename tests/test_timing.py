import unittest

from custom_components.fourvrs_server_room.core.models import Mode
from custom_components.fourvrs_server_room.core.timing import TimingGate


class TimingTests(unittest.TestCase):
    def test_continuous_condition_required(self):
        gate = TimingGate(condition_hold=60, minimum_dwell=0)
        self.assertFalse(gate.update(Mode.VENTILATION, now=0).allowed)
        gate.update(None, now=30)
        self.assertFalse(gate.update(Mode.VENTILATION, now=60).allowed)
        self.assertTrue(gate.update(Mode.VENTILATION, now=120).allowed)

    def test_both_dwell_and_condition_must_expire(self):
        gate = TimingGate(condition_hold=60, minimum_dwell=180)
        gate.confirmed(now=0)
        gate.update(Mode.AIR_CONDITIONING, now=1)
        self.assertFalse(gate.update(Mode.AIR_CONDITIONING, now=61).allowed)
        self.assertTrue(gate.update(Mode.AIR_CONDITIONING, now=180).allowed)

    def test_urgent_skips_only_optimization_timers(self):
        gate = TimingGate(condition_hold=60, minimum_dwell=180)
        gate.confirmed(now=0)
        self.assertTrue(gate.update(Mode.AIR_CONDITIONING, now=1, urgent=True).allowed)
        self.assertFalse(gate.update(None, now=2, urgent=True).allowed)

    def test_clock_reversal_restarts_timing(self):
        gate = TimingGate(condition_hold=60, minimum_dwell=180)
        gate.update(Mode.VENTILATION, now=100)
        self.assertFalse(gate.update(Mode.VENTILATION, now=0).allowed)
        self.assertEqual(gate.update(Mode.VENTILATION, now=0).dwell_remaining, 180)
