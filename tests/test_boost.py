import unittest
from custom_components.fourvrs_server_room.core.boost import emergency_target


class BoostTests(unittest.TestCase):
    def test_cold_inlet_requests_lower_target_and_respects_limits(self):
        self.assertEqual(emergency_target(22, 21.5, 16, 30, 1), 20)
        self.assertEqual(emergency_target(22, 16.5, 16, 30, 1), 16)
        self.assertEqual(emergency_target(22, 23, 16, 30, 1), 22)
        self.assertEqual(emergency_target(22, 21, 16, 30, .5), 20)

    def test_no_guessed_limits_or_invalid_values(self):
        for args in ((22, None, 16, 30, 1), (22, 21, None, 30, 1),
                     (22, 21, 16, 30, None), (22, 21, 16, 30, 0),
                     (22, float('nan'), 16, 30, 1), (22, 21, 25, 30, 1)):
            self.assertIsNone(emergency_target(*args))
