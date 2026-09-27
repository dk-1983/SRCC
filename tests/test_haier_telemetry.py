"""Schema, clocks, source freshness and firmware command evidence."""
import unittest
from custom_components.fourvrs_server_room.haier_telemetry import HaierTelemetry, Frame
from custom_components.fourvrs_server_room.core.models import Quality


def packet(**changes):
    data = dict(schema=1, boot_id="a"*32, status_frames=10, age_ms=0,
                sample_uptime_ms=10000, published_uptime_ms=10000,
                available=True, mode="COOL", target_temperature=22,
                current_temperature=23, fault_valid=True, fault=False, fault_code=0,
                request_id="mqtt-1", command_state="confirmed")
    data.update(changes)
    return data


class HaierTelemetryTests(unittest.TestCase):
    def test_validation_rejects_wrong_schema_types_clocks_and_fault(self):
        bad = [dict(schema=2), dict(schema=True), dict(boot_id="bad"),
               dict(status_frames=True), dict(sample_uptime_ms=-1),
               dict(published_uptime_ms=9999), dict(age_ms=float("nan")),
               dict(target_temperature=float("inf")), dict(available="true"),
               dict(fault_valid="true"), dict(fault=False, fault_code=1),
               dict(fault_valid=False, fault=False), dict(fault_code=256)]
        for changes in bad:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                Frame.parse(packet(**changes))
        for code in (0, 1, 255):
            self.assertEqual(Frame.parse(packet(fault_code=code, fault=bool(code))).fault_code, code)

    def test_live_packet_required_and_independent_heartbeat_and_source_expiry(self):
        cache = HaierTelemetry()
        self.assertIs(cache.quality(100), Quality.UNVERIFIED)
        cache.receive(packet(), 100)
        self.assertIs(cache.quality(114.999), Quality.VALID)
        self.assertIs(cache.quality(115), Quality.STALE)
        for elapsed in (5, 10, 15, 20, 25, 30):
            cache.receive(packet(published_uptime_ms=10000+elapsed*1000, age_ms=elapsed*1000), 100+elapsed)
        self.assertEqual(cache.measured_at, 100)
        self.assertIs(cache.quality(130), Quality.STALE)
        self.assertEqual(cache.diagnostics(130)["heartbeat_age_s"], 0)

    def test_long_unchanged_values_with_new_uart_frames_stay_fresh(self):
        cache = HaierTelemetry()
        for elapsed in range(0, 601, 5):
            self.assertTrue(cache.receive(packet(status_frames=10+elapsed, sample_uptime_ms=10000+elapsed*1000,
                                                 published_uptime_ms=10000+elapsed*1000), 100+elapsed))
            self.assertIs(cache.quality(100+elapsed), Quality.VALID)
        self.assertEqual(cache.samples(700)[1].value, 22)

    def test_repeats_delays_and_backwards_packets_cannot_rejuvenate(self):
        cache = HaierTelemetry()
        cache.receive(packet(age_ms=1000, published_uptime_ms=11000), 100)
        self.assertEqual(cache.measured_at, 99)
        cache.receive(packet(age_ms=2000, published_uptime_ms=12000), 110)
        self.assertEqual(cache.measured_at, 99)
        self.assertFalse(cache.receive(packet(), 111))
        self.assertIs(cache.quality(111), Quality.INVALID)
        self.assertFalse(cache.receive(packet(age_ms=3000, published_uptime_ms=13000, target_temperature=23), 112))

    def test_heartbeat_cannot_change_fault_without_new_uart(self):
        cache = HaierTelemetry()
        cache.receive(packet(fault=True, fault_code=1), 100)
        self.assertFalse(cache.receive(packet(published_uptime_ms=11000, age_ms=1000), 101))
        self.assertIs(cache.quality(101), Quality.INVALID)

    def test_incomplete_fault_is_unknown_not_false(self):
        cache = HaierTelemetry()
        cache.receive(packet(fault_valid=False, fault=None, fault_code=None), 100)
        mode, target, fault = cache.samples(100)
        self.assertIs(mode.quality, Quality.VALID)
        self.assertIsNone(fault.value)
        self.assertIs(fault.quality, Quality.UNKNOWN)
        with self.assertRaises(ValueError):
            cache.arm("ac_cool", 100)

    def test_restart_invalidates_command_and_old_boot_is_rejected(self):
        cache = HaierTelemetry()
        cache.receive(packet(), 100)
        cache.arm("ac_set_target", 100.1)
        cache.receive(packet(boot_id="b"*32, status_frames=1, sample_uptime_ms=10, published_uptime_ms=10), 101)
        self.assertEqual(cache.command_error, "HAIER_RESTART_DURING_COMMAND")
        self.assertFalse(cache.confirmed(101))
        self.assertFalse(cache.receive(packet(published_uptime_ms=11000, age_ms=1000), 102))

    def test_confirmation_requires_accepted_result_new_sample_and_matching_id(self):
        cache = HaierTelemetry()
        cache.receive(packet(), 100)
        cache.arm("ac_set_target", 100.1)
        cache.result(dict(status="confirmed", request_id="mqtt-2"), 100.2)
        self.assertFalse(cache.confirmed(100.2))
        cache.result(dict(status="accepted", request_id="mqtt-2", field="target"), 100.3, retained=True)
        self.assertIsNone(cache.command["request_id"])
        cache.result(dict(status="accepted", request_id="mqtt-2", field="target"), 100.4)
        cache.result(dict(status="confirmed", request_id="mqtt-2"), 100.5)
        cache.receive(packet(published_uptime_ms=11000, age_ms=1000, request_id="mqtt-2"), 101)
        self.assertFalse(cache.confirmed(101))
        cache.receive(packet(status_frames=12, sample_uptime_ms=12000, published_uptime_ms=12000,
                             request_id="mqtt-2"), 102)
        self.assertTrue(cache.confirmed(102))
        self.assertFalse(cache.confirmed(118))

    def test_foreign_command_rejection_and_firmware_timeout(self):
        for result, error in [(dict(status="accepted", request_id="mqtt-2", field="fan"), "HAIER_COMMAND_CONFLICT"),
                              (dict(status="rejected", field="target"), "HAIER_COMMAND_REJECTED")]:
            cache = HaierTelemetry(); cache.receive(packet(), 100); cache.arm("ac_set_target", 100.1)
            cache.result(result, 100.2)
            self.assertEqual(cache.command_error, error)
        cache = HaierTelemetry(); cache.receive(packet(), 100); cache.arm("ac_set_target", 100.1)
        cache.result(dict(status="accepted", request_id="mqtt-2", field="target"), 100.2)
        cache.result(dict(status="timeout_unconfirmed", request_id="mqtt-2"), 101)
        self.assertEqual(cache.command_error, "HAIER_COMMAND_UNCONFIRMED")

    def test_before_first_uart_and_unavailable_are_not_valid(self):
        cache = HaierTelemetry()
        self.assertTrue(cache.receive(packet(status_frames=0, sample_uptime_ms=0, age_ms=None,
                                             available=False, mode=None, target_temperature=None,
                                             fault_valid=False, fault=None, fault_code=None), 100))
        self.assertIs(cache.quality(100), Quality.UNAVAILABLE)
