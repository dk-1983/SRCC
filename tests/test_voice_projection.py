"""Semantic notification tests, independent of Home Assistant or equipment."""
import unittest
from types import SimpleNamespace as NS

from custom_components.fourvrs_server_room.voice_events import VoiceProjection
from custom_components.fourvrs_server_room.voice_details import ROLES, REASONS
from custom_components.fourvrs_server_room.core.models import Mode, Power
from custom_components.fourvrs_server_room.core.transitions import Step


def runtime():
    data = {role: "sensor." + role for role in ROLES}
    data.update(ac_target=22, max_age=120)
    return NS(data=data, haier=None, vent_failed=False, reported_mode="air_conditioning",
        hass=NS(states=NS(get=lambda entity: NS(attributes={"friendly_name": "Имя " + entity.split(".")[-1]}))),
        input_status={role: {"quality": "valid"} for role in ROLES},
        snapshot=NS(now=0, power=Power.MAINS, ac_fault=False, proved=lambda *a: True, vent_output=None),
        status=NS(alarm=NS(thermal_critical=False, reason="TEMPERATURE_NORMAL"),
                  temperature=NS(sample=NS(source=data["trm_temperature"]), reason="TRM_PRIMARY", degraded=False),
                  decision=NS(desired=Mode.AIR_CONDITIONING, urgent=False, reason="HOLD_AC"), degraded=False),
        executor=NS(enabled=True, error=None, step=Step("COMPLETE", complete=True),
                    last_command=None, target=Mode.AIR_CONDITIONING))


class VoiceProjectionTests(unittest.TestCase):
    def setUp(self):
        self.rt = runtime()
        self.p = VoiceProjection()
        self.p.update(self.rt)

    def test_every_configured_role_identified_and_distinct_quality(self):
        for role in ROLES:
            with self.subTest(role=role):
                rt = runtime(); p = VoiceProjection(); p.update(rt)
                rt.input_status[role]["quality"] = "stale"
                p.update(rt); rt.snapshot.now = 21
                issues, _ = p.update(rt)
                message = issues["input_" + role]["message"]
                self.assertIn(ROLES[role], message)
                self.assertIn("не обновляются", message)
                self.assertIn("Имя", message)
                self.assertTrue(message.startswith("Климат серверной."))
                rt.input_status[role]["quality"] = "unavailable"
                self.assertIn("недоступна", p.update(rt)[0]["input_" + role]["message"])

    def test_fallback_mean_single_and_stable_return(self):
        rt = self.rt
        rt.input_status["trm_temperature"]["quality"] = "stale"
        rt.status.temperature = NS(sample=NS(source="mean"), reason="BOLID_MEAN_FALLBACK", degraded=True)
        issues, _ = self.p.update(rt)
        self.assertNotIn("input_trm_temperature", issues)
        self.assertIn("среднее", issues["temperature_source"]["message"])
        rt.status.temperature = NS(sample=NS(source=rt.data["bolid_temperature_2"]), reason="BOLID_SINGLE_FALLBACK", degraded=True)
        changed, _ = self.p.update(rt)
        self.assertIn("№ 2", changed["temperature_source"]["message"])
        self.assertNotEqual(issues, changed)
        rt.input_status["trm_temperature"]["quality"] = "valid"
        rt.status.temperature = NS(sample=NS(source=rt.data["trm_temperature"]), reason="TRM_PRIMARY", degraded=False)
        self.assertIn("temperature_source", self.p.update(rt)[0])
        rt.snapshot.now = 21
        current, notices = self.p.update(rt)
        self.assertNotIn("temperature_source", current)
        self.assertIn("Основной", next(n["message"] for n in notices if n["key"] == "temperature_source"))

    def test_unused_backup_flap_does_not_speak(self):
        self.rt.input_status["bolid_temperature_1"]["quality"] = "unavailable"
        self.assertEqual(self.p.update(self.rt), ({}, []))
        self.rt.snapshot.now = 10
        self.rt.input_status["bolid_temperature_1"]["quality"] = "valid"
        self.assertEqual(self.p.update(self.rt), ({}, []))

    def test_input_recovery_waits_for_stability(self):
        self.rt.input_status["outdoor_temperature"]["quality"] = "unavailable"
        self.p.update(self.rt)
        self.rt.input_status["outdoor_temperature"]["quality"] = "valid"
        self.assertIn("input_outdoor_temperature", self.p.update(self.rt)[0])
        self.rt.snapshot.now = 21
        current, notices = self.p.update(self.rt)
        self.assertNotIn("input_outdoor_temperature", current)
        self.assertIn("наружной", notices[0]["message"])

    def test_no_temperature_is_error_and_no_duplicate_blocker(self):
        self.rt.status.temperature.sample = None
        self.rt.input_status["trm_temperature"]["quality"] = "unavailable"
        self.rt.executor.step = Step("BLOCKED", reason="TEMPERATURE_UNTRUSTED")
        issues, _ = self.p.update(self.rt)
        self.assertEqual(issues["temperature_source"]["level"], "ERROR")
        self.assertNotIn("action_deferred", issues)
        self.rt.executor.enabled = False
        self.assertEqual(self.p.update(self.rt)[0]["temperature_source"]["level"], "WARNING")

    def test_haier_shared_telemetry_yields_one_input_issue(self):
        self.rt.haier = object()
        for role in ("climate", "ac_fault", "ac_telemetry"):
            self.rt.input_status[role]["quality"] = "stale"
        issues, _ = self.p.update(self.rt)
        self.assertEqual(list(issues), ["input_ac_telemetry"])

    def test_ups_battery_unknown_conflict_and_mains_are_distinct(self):
        messages = []
        for power in (Power.BATTERY, Power.UNKNOWN, Power.CONFLICT):
            self.rt.snapshot.power = power
            issues, _ = self.p.update(self.rt)
            messages.append(issues["power_supply"]["message"])
        self.assertEqual(len(set(messages)), 3)
        self.rt.input_status["ups_online"]["quality"] = "stale"
        self.rt.executor.step = Step("BLOCKED", reason="POWER_POLICY_REQUIRED")
        issues, _ = self.p.update(self.rt)
        self.assertIn("не обновляются", issues["power_supply"]["message"])
        self.assertNotIn("input_ups_online", issues)
        self.assertNotIn("action_deferred", issues)
        self.rt.snapshot.power = Power.MAINS
        self.rt.executor.step = Step("COMPLETE", complete=True)
        _, notices = self.p.update(self.rt)
        self.assertTrue(any(n["key"] == "power_supply" for n in notices))

    def test_every_block_reason_has_specific_text_and_urgent_escalation(self):
        for reason in REASONS:
            self.rt.executor.step = Step("BLOCKED", reason=reason)
            issues, _ = self.p.update(self.rt)
            self.assertIn(REASONS[reason], issues["action_deferred"]["message"])
        self.rt.status.decision.urgent = True
        self.assertEqual(self.p.update(self.rt)[0]["action_deferred"]["level"], "ERROR")

    def test_normal_wait_info_once_and_fault_wait_warning(self):
        for phase in ("WAIT_TIMING", "WAIT_AC_MINIMUM_OFF"):
            self.rt.executor.step = Step(phase)
            current, notices = self.p.update(self.rt)
            self.assertNotIn("action_deferred", current)
            self.assertEqual(notices[-1]["level"], "INFO")
            self.assertEqual(self.p.update(self.rt)[1], [])
        self.rt.executor.step = Step("WAIT_HAIER_COMMAND", reason="COMMAND_CONFLICT_RECOVERY")
        self.assertIn("Конкурирующая", self.p.update(self.rt)[0]["action_deferred"]["message"])

    def test_execution_error_names_action_and_does_not_claim_recovery(self):
        self.rt.executor.step = Step("BLOCKED", reason="AC_OFF_UNPROVEN")
        self.p.update(self.rt)
        self.rt.executor.error = "READBACK_TIMEOUT"
        self.rt.executor.last_command = dict(action="ac_off")
        current, notices = self.p.update(self.rt)
        self.assertIn("выключение кондиционера", current["execution_failure"]["message"])
        self.assertFalse(any(n["key"] == "action_deferred" for n in notices))

    def test_setpoint_info_only_after_readback_complete(self):
        self.rt.executor.last_command = dict(sequence=1, status="awaiting_readback", action="ac_set_target")
        self.assertEqual(self.p.update(self.rt)[1], [])
        self.rt.executor.last_command["status"] = "confirmed"
        notices = self.p.update(self.rt)[1]
        self.assertIn("22", next(n["message"] for n in notices if n["key"] == "target_restored"))
        self.assertEqual(self.p.update(self.rt)[1], [])

    def test_message_bounds_and_no_raw_exception(self):
        self.rt.hass.states.get = lambda _: NS(attributes={"friendly_name": "x" * 5000})
        for role in ROLES:
            self.rt.input_status[role]["quality"] = "invalid"
        self.rt.executor.error = "secret token arbitrary exception"
        current, _ = self.p.update(self.rt)
        self.assertTrue(all(len(v["message"]) <= 500 for v in current.values()))
        self.assertNotIn("secret", str(current))
