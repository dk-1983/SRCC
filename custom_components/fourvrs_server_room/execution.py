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
        self.last_ac_mode = None
        self.last_off = monotonic()
        self.timing = None
        self.wait_details = {}
        self.last_command = None

    def enable(self):
        if not self.runtime.data.get("control_profile_enabled"):
            raise HomeAssistantError("Configure the execution profile first")
        validate(self.runtime.data)
        self.generation += 1
        self.enabled, self.error = True, None
        self.runtime.vent_failed = False
        self.planner, self.target = None, None
        self.last_off = monotonic()
        self.timing = TimingGate(condition_hold=self.runtime.data["condition_hold"],
                                 minimum_dwell=self.runtime.data["minimum_dwell"])
        self.runtime.telemetry.record("automation_enabled")
        self.runtime.refresh()

    def disable(self):
        was_enabled = self.enabled
        self.enabled = False
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
                                 timeout=data["command_timeout"], ac_target=data["ac_target"],
                                 manual_fan=True, combined_cool_target=True)

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
                state = self.runtime.snapshot
                status = self.runtime.status
                state = replace(state, vent_ineffective=state.vent_ineffective or status.trend.ineffective is True)
                selected = status.temperature.sample
                if selected is not None:
                    state = replace(state, tin=selected)
                now = state.now
                ac_mode = state.ac_mode.value if state.ac_mode.fresh(now, self.runtime.data["max_age"]) else None
                if ac_mode != self.last_ac_mode:
                    if ac_mode != "cool":
                        self.last_off = now
                    self.last_ac_mode = ac_mode
                target = status.decision.desired
                if state.power != self.last_power:
                    self.planner, self.target = None, None
                    self.last_power = state.power
                if target is None or target is Mode.NO_COOLING_AVAILABLE:
                    # Don't drop an outstanding readback silently: it must
                    # still time out if telemetry or policy disappears.
                    if self.planner and self.planner.pending is not None:
                        if now - self.planner.sent_at >= self.runtime.data["command_timeout"]:
                            raise HomeAssistantError("READBACK_TIMEOUT")
                    self.step = Step("BLOCKED", reason=status.decision.reason)
                    return
                if target != self.target or self.planner is None:
                    self.planner = self._new_planner()
                    self.target = target
                timing = self.timing.update(target, now=now, urgent=status.decision.urgent)

                # Returning mains may auto-start AC. Do not dwell while
                # reconciling unknown/conflicting equipment, or on battery.
                reconcile = self.runtime.reported_mode in ("unknown", "conflict") or state.power is Power.BATTERY
                # Maintaining a confirmed AC mode is not a seasonal change.
                # Recheck every launch/readback guard, but do not delay a
                # setpoint correction or report WAIT_TIMING for an achieved goal.
                maintaining_ac = target is Mode.AIR_CONDITIONING and state.current is Mode.AIR_CONDITIONING
                allowed = timing.allowed or reconcile or maintaining_ac
                previous_pending = self.planner.pending
                pending_since = self.planner.sent_at
                self.step = self.planner.advance(state, target, automatic=True,
                                                 ready=selected is not None, timing_allowed=allowed)
                if previous_pending and self.planner.pending is not previous_pending:
                    self.runtime.telemetry.record("command_confirmed", action=previous_pending.value,
                                                  elapsed_s=round(now - pending_since, 3))
                    if self.last_command:
                        self.last_command = dict(self.last_command, status="confirmed")
                if self.step.phase == "WAIT_TIMING":
                    self.wait_details = {"condition_remaining_s": round(timing.condition_remaining, 1),
                                         "dwell_remaining_s": round(timing.dwell_remaining, 1)}
                if self.step.phase == "WAIT_READBACK":
                    self.wait_details = {"readback_remaining_s": round(max(0, self.runtime.data["command_timeout"]
                                                              - (now - self.planner.sent_at)), 1)}
                if self.step.phase == "BLOCKED" and self.planner.error:
                    raise HomeAssistantError(self.planner.error)
                if self.step.complete:
                    if not getattr(self.planner, "committed", False):
                        self.timing.confirmed(now=now)
                        self.planner.committed = True
                    return
                action = self.step.action
                if action is None:
                    return
                if action in (Action.AC_COOL, Action.AC_SET_TARGET) and ac_mode != "cool":
                    if now - self.last_off < self.runtime.data["minimum_ac_off"]:
                        self.planner.pending = None
                        self.wait_details = {"ac_off_remaining_s": round(self.runtime.data["minimum_ac_off"] - (now - self.last_off), 1)}
                        self.step = Step("WAIT_AC_MINIMUM_OFF")
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
                # Set COOL and target together, only after fan OFF and all
                # launch guards. Avoid an ambiguous setpoint-only command.
                target = data["ac_target"]
                if rt.hass.config.units.temperature_unit == "°F":
                    target = target * 9 / 5 + 32
                state = rt.hass.states.get(entity_id)
                if state is None or "cool" not in state.attributes.get("hvac_modes", []):
                    raise HomeAssistantError("AC_COOL_UNSUPPORTED")
                if not state.attributes.get("min_temp", float("-inf")) <= target <= state.attributes.get("max_temp", float("inf")):
                    raise HomeAssistantError("AC_TARGET_OUT_OF_RANGE")
                service = "set_temperature"
                payload = {"entity_id": entity_id, "temperature": target, "hvac_mode": "cool"}
        if not rt.hass.services.has_service(domain, service):
            raise HomeAssistantError("SERVICE_UNAVAILABLE:" + domain + "." + service)
        role = "vent_enable" if action in (Action.VENT_DISABLE, Action.VENT_ENABLE) else (
            "vent_output" if action in (Action.FAN_ON, Action.FAN_OFF) else "climate")
        parameters = {key: value for key, value in payload.items() if key != "entity_id"}
        event = rt.telemetry.record("command_sent", action=action.value, role=role,
                                    service=domain + "." + service, parameters=parameters)
        self.last_command = {"sequence": event["sequence"], "time": event["time"],
                             "action": action.value, "role": role, "status": "sent"}
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
