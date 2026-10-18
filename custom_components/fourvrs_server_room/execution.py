"""Serial HA command dispatcher. Restored intent starts a new equipment reconciliation."""
import asyncio
from dataclasses import replace
from time import monotonic

from homeassistant.exceptions import HomeAssistantError

from .configuration import core_config, validate
from .core.models import Mode, Power
from .core.timing import TimingGate
from .core.transitions import Action, Step, TransitionPlanner


class Executor:
    def __init__(self, runtime):
        self.runtime = runtime
        self.enabled = False
        self.error = None
        self.step = Step("OBSERVATION_ONLY")
        self.task = None
        self.planner = None
        self.target = None
        self.generation = 0
        self.last_power = None
        self.committed_mode = None
        self.timing = None
        self.wait_details = {}
        self.last_command = None
        self.haier_recovery = None

    def enable(self):
        if not self.runtime.data.get("control_profile_enabled"):
            raise HomeAssistantError("Configure the execution profile first")
        validate(self.runtime.data)
        self.generation += 1
        self.enabled, self.error = True, None
        self.haier_recovery = None
        if self.runtime.haier:
            self.runtime.haier.command = None
            self.runtime.haier.command_error = None
            self.runtime.haier.command_error_detail = None
        self.runtime.vent_failed = False
        self.planner, self.target = None, None
        self.committed_mode = None
        self.timing = TimingGate(condition_hold=self.runtime.data["condition_hold"],
                                 minimum_dwell=self.runtime.data["minimum_dwell"])
        self.runtime.telemetry.record("automation_enabled")
        self.runtime.refresh()

    def disable(self):
        if self.runtime.haier:
            self.runtime.haier.command = None
            self.runtime.haier.command_error = None
            self.runtime.haier.command_error_detail = None
        was_enabled = self.enabled
        self.enabled = False
        self.haier_recovery = None
        self.generation += 1
        self.planner, self.target = None, None
        self.step = Step("OBSERVATION_ONLY")
        self.wait_details = {}
        if was_enabled:
            self.runtime.telemetry.record("automation_disabled")
        # An already submitted HA service cannot be recalled. No next command
        # is sent; a new enable starts from current equipment feedback.
        self.runtime.telemetry.execution_changed(self)
        self.runtime.notify()

    def schedule(self):
        if self.enabled and not self.error and not self.runtime.stopped:
            if self.task is None or self.task.done():
                self.task = self.runtime.hass.async_create_task(self._run(), eager_start=False)

    def _new_planner(self):
        data = self.runtime.data
        return TransitionPlanner(core_config(data), max_age=data["max_age"],
                                 timeout=data["command_timeout"], ac_target=self.runtime.active_ac_target,
                                 manual_fan=True, live_target_updates=True,
                                 ac_readback_timeout=(data.get("haier_readback_timeout", 35.0) if self.runtime.haier
                                                      else data.get("ac_readback_timeout", 3.0)))

    def _recover_haier_conflict(self):
        """Abandon ambiguous ownership, never treat a foreign result as success."""
        rt, haier = self.runtime, self.runtime.haier
        if not haier:
            return False
        now = rt.snapshot.now
        recoverable = ("HAIER_COMMAND_CONFLICT", "HAIER_COMMAND_SUPERSEDED")
        if self.haier_recovery is None:
            if haier.command_error not in recoverable:
                return False
            self.haier_recovery = dict(
                since=now, boot=haier.command["boot"] if haier.command else haier.frame.boot,
                ready_at=now + max(5.0, rt.data.get("haier_readback_timeout", 35.0)))
            if self.last_command:
                self.last_command = dict(self.last_command, status="conflict")
            rt.telemetry.record("command_conflict", warning=True,
                                reason=haier.command_error, detail=haier.command_error_detail)
        recovery = self.haier_recovery
        if (haier.frame.command_state == "timeout_unconfirmed"
                and haier.received_at >= recovery["since"]):
            raise HomeAssistantError("HAIER_COMMAND_UNCONFIRMED")
        if haier.frame.boot != recovery["boot"]:
            raise HomeAssistantError("HAIER_RESTART_DURING_COMMAND")
        # A complete readback window lets an already submitted command finish.
        # Result traffic adds a quiet period; a heartbeat alone cannot release us.
        remaining = max(recovery["ready_at"] - now,
                        (haier.last_result_at or recovery["since"]) + 5.0 - now, 0)
        if (remaining > 0 or not rt.snapshot.ac_available
                or not haier.frame.fault_valid
                or haier.frame.command_state != "confirmed"
                or haier.measured_at <= recovery["since"]):
            self.step = Step("WAIT_HAIER_COMMAND", reason="COMMAND_CONFLICT_RECOVERY")
            self.wait_details = {"conflict_recovery_remaining_s": round(remaining, 1)}
            return True
        rt.telemetry.record("command_reconciliation", reason=haier.command_error,
                            detail=haier.command_error_detail)
        haier.command = None
        haier.command_error = None
        haier.command_error_detail = None
        self.haier_recovery = None
        self.planner, self.target = None, None
        # Normal policy, power, interlock and compressor guards run again below.
        return False

    async def _run(self):
        generation = self.generation
        try:
            # Each iteration refreshes input and guards after the preceding
            # awaited service. A single task owns all command dispatch.
            for _ in range(12):
                if not self.enabled or generation != self.generation or self.runtime.stopped:
                    return
                self.wait_details = {}
                self.runtime.refresh()
                if self._recover_haier_conflict():
                    return
                if self.runtime.haier and self.runtime.haier.command_error:
                    raise HomeAssistantError(self.runtime.haier.command_error)
                state = self.runtime.snapshot
                status = self.runtime.status
                # A manual MQTT/web/Modbus command may still be in flight.
                # Wait for its firmware outcome before planning our next action.
                haier = self.runtime.haier
                if (haier and state.ac_available and haier.frame.command_state == "pending"
                        and haier.command is None
                        and (self.planner is None or self.planner.pending is None)):
                    self.step = Step("WAIT_HAIER_COMMAND", reason="EXTERNAL_COMMAND_PENDING")
                    return
                state = replace(state, thermal_critical=status.alarm.thermal_critical,
                                vent_ineffective=state.vent_ineffective or status.trend.ineffective is True)
                selected = status.temperature.sample
                if selected is not None:
                    state = replace(state, tin=selected)
                now = state.now
                target = status.decision.desired
                # A policy/power change must not erase an unresolved command.
                if (self.planner and self.planner.pending is not None
                        and (target != self.target or state.power != self.last_power
                             or self.planner.ac_target != self.runtime.active_ac_target)):
                    pending = self.planner.pending
                    elapsed = now - self.planner.sent_at
                    if self.planner._confirmed(state, pending, self.planner.sent_at):
                        self.runtime.telemetry.record("command_confirmed", action=pending.value,
                                                      elapsed_s=round(elapsed, 3))
                        if self.last_command:
                            self.last_command = dict(self.last_command, status="confirmed")
                        if pending is Action.AC_SET_TARGET:
                            self.runtime.adaptation.confirmed(self.planner.ac_target)
                        self.planner.pending = None
                        if self.runtime.haier:
                            self.runtime.haier.command = None
                    elif elapsed >= self.planner.pending_timeout:
                        raise HomeAssistantError("READBACK_TIMEOUT")
                    else:
                        self.step = Step("WAIT_READBACK", reason=pending.value)
                        self.wait_details = {"readback_remaining_s": round(
                            self.planner.pending_timeout - elapsed, 1)}
                        return
                if state.power != self.last_power:
                    self.planner, self.target = None, None
                    self.last_power = state.power
                if target is None or target is Mode.NO_COOLING_AVAILABLE:
                    # Don't drop an outstanding readback silently: it must
                    # still time out if telemetry or policy disappears.
                    if self.planner and self.planner.pending is not None:
                        if now - self.planner.sent_at >= self.planner.pending_timeout:
                            raise HomeAssistantError("READBACK_TIMEOUT")
                    self.step = Step("BLOCKED", reason=status.decision.reason)
                    return
                if (target != self.target or self.planner is None
                        or self.planner.ac_target != self.runtime.active_ac_target):
                    self.planner = self._new_planner()
                    self.target = target
                timing = self.timing.update(target, now=now, urgent=status.decision.urgent)

                # Returning mains may auto-start AC. Do not dwell while
                # reconciling unknown/conflicting equipment, or on battery.
                reconcile = self.runtime.reported_mode in ("unknown", "conflict") or state.power is Power.BATTERY
                # Maintaining a confirmed cooling mode is not a seasonal change.
                # Recheck every launch/readback guard, but do not delay a
                # setpoint correction or report WAIT_TIMING for an achieved goal.
                maintaining = target is state.current
                # Confirmed idle is not an active cooling method to preserve.
                # Restore cooling without seasonal optimization delays.
                restoring = state.current is Mode.IDLE and target in (
                    Mode.AIR_CONDITIONING, Mode.VENTILATION, Mode.EMERGENCY_VENTILATION)
                allowed = timing.allowed or reconcile or maintaining or restoring
                previous_pending = self.planner.pending
                pending_since = self.planner.sent_at
                self.step = self.planner.advance(state, target, automatic=True,
                                                 ready=selected is not None, timing_allowed=allowed)
                if previous_pending and self.planner.pending is not previous_pending:
                    if self.runtime.haier:
                        self.runtime.haier.command = None
                    self.runtime.telemetry.record("command_confirmed", action=previous_pending.value,
                                                  elapsed_s=round(now - pending_since, 3))
                    if self.last_command:
                        self.last_command = dict(self.last_command, status="confirmed")
                    if previous_pending is Action.AC_SET_TARGET:
                        self.runtime.adaptation.confirmed(self.planner.ac_target)
                if self.step.phase == "WAIT_TIMING":
                    self.wait_details = {"condition_remaining_s": round(timing.condition_remaining, 1),
                                         "dwell_remaining_s": round(timing.dwell_remaining, 1)}
                if self.step.phase == "WAIT_READBACK":
                    self.wait_details = {"readback_remaining_s": round(max(0, self.planner.pending_timeout
                                                              - (now - self.planner.sent_at)), 1)}
                if self.step.phase == "BLOCKED" and self.planner.error:
                    raise HomeAssistantError(self.planner.error)
                if self.step.complete:
                    if not getattr(self.planner, "committed", False):
                        if self.committed_mode is not target:
                            self.timing.confirmed(now=now)
                            self.committed_mode = target
                        self.planner.committed = True
                    return
                action = self.step.action
                if action is None:
                    return
                if not self.enabled or generation != self.generation or self.runtime.stopped:
                    return
                await self._send(action)
                # Synchronous feedback is processed next. Otherwise the next
                # iteration returns WAIT_READBACK until a state event/tick.
        except asyncio.CancelledError:
            raise
        except Exception as error:
            if generation == self.generation and not self.runtime.stopped:
                self.error = str(error) or type(error).__name__
                self.step = Step("ERROR", reason=self.error)
                if self.planner:
                    self.planner.fail(self.error)
        finally:
            self.runtime.telemetry.execution_changed(self)
            self.runtime.notify()

    async def _send(self, action):
        rt, data = self.runtime, self.runtime.data
        if action is Action.AC_SET_TARGET:
            await rt.adaptation.before_send(self.planner.ac_target)
            if not self.enabled or rt.stopped:
                return
        if action in (Action.VENT_DISABLE, Action.VENT_ENABLE):
            entity_id = data["vent_enable"]
            domain = entity_id.split(".")[0]
            enable = action is Action.VENT_ENABLE
            service = "set_value" if domain == "number" else ("turn_on" if enable else "turn_off")
            payload = {"entity_id": entity_id}
            if domain == "number":
                payload["value"] = 1 if enable else 0
        elif action in (Action.FAN_ON, Action.FAN_OFF):
            domain, entity_id = "switch", data["vent_output"]
            service = "turn_on" if action is Action.FAN_ON else "turn_off"
            payload = {"entity_id": entity_id}
        else:
            domain, entity_id = "climate", data["climate"]
            if action is Action.AC_OFF:
                service, payload = "set_hvac_mode", {"entity_id": entity_id, "hvac_mode": "off"}
            else:
                # Separate mode and temperature commands; each has its own
                # readback. Some upstream drivers do not apply both at once.
                target = self.planner.ac_target
                if rt.hass.config.units.temperature_unit == "°F":
                    target = target * 9 / 5 + 32
                state = rt.hass.states.get(entity_id)
                if state is None or "cool" not in state.attributes.get("hvac_modes", []):
                    raise HomeAssistantError("AC_COOL_UNSUPPORTED")
                if not state.attributes.get("min_temp", float("-inf")) <= target <= state.attributes.get("max_temp", float("inf")):
                    raise HomeAssistantError("AC_TARGET_OUT_OF_RANGE")
                if action is Action.AC_COOL:
                    service = "set_hvac_mode"
                    payload = {"entity_id": entity_id, "hvac_mode": "cool"}
                else:
                    service = "set_temperature"
                    payload = {"entity_id": entity_id, "temperature": target}
        if not rt.hass.services.has_service(domain, service):
            raise HomeAssistantError("SERVICE_UNAVAILABLE:" + domain + "." + service)
        role = "vent_enable" if action in (Action.VENT_DISABLE, Action.VENT_ENABLE) else (
            "vent_output" if action in (Action.FAN_ON, Action.FAN_OFF) else "climate")
        parameters = {key: value for key, value in payload.items() if key != "entity_id"}
        if rt.haier and role == "climate":
            if not rt.haier_result_ready:
                raise HomeAssistantError("HAIER_RESULT_SUBSCRIPTION_MISSING")
            rt.haier.arm(action.value, monotonic())
        event = rt.telemetry.record("command_sent", action=action.value, role=role,
                                    service=domain + "." + service, parameters=parameters)
        self.last_command = {"sequence": event["sequence"], "time": event["time"],
                             "action": action.value, "role": role, "status": "sent",
                             "target": self.planner.ac_target if role == "climate" else None}
        try:
            async with asyncio.timeout(data["command_timeout"]):
                await rt.hass.services.async_call(domain, service, payload, blocking=True)
        except Exception as error:
            self.last_command = dict(self.last_command, status="service_failed")
            rt.telemetry.record("command_failed", warning=True, action=action.value,
                                role=role, error_type=type(error).__name__)
            raise
        self.last_command = dict(self.last_command, status="awaiting_readback")
        rt.telemetry.record("service_completed", action=action.value, role=role)
