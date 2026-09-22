"""Bounded diagnostics without live equipment or HA dependencies."""
import unittest
from unittest.mock import patch
from custom_components.fourvrs_server_room.telemetry import Telemetry

class TelemetryTests(unittest.TestCase):
    def test_recent_history_is_bounded_and_ordered(self):
        journal = Telemetry()
        with patch("custom_components.fourvrs_server_room.telemetry._LOGGER"):
            for index in range(120):
                journal.record("decision", reason=str(index))
        self.assertEqual(len(journal.events), 100)
        self.assertEqual(journal.events[0]["sequence"], 21)
        self.assertEqual(journal.events[-1]["sequence"], 120)
