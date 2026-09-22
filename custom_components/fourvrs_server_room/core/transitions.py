"""Single-flight readback planner. No services, transports, retries or I/O.

An Action is an intent, not evidence of execution. The HA executor serializes
planning and dispatch, rechecks its generation, and reports failures with fail().
"""
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from .models import Config, Mode, Power, Snapshot


class Action(str, Enum):
    AC_OFF = "ac_off"
    VENT_DISABLE = "vent_disable"
    VENT_ENABLE = "vent_enable"
    AC_SET_TARGET = "ac_set_target"
    AC_COOL = "ac_cool"
    FAN_OFF = "fan_off"
    FAN_ON = "fan_on"


@dataclass(frozen=True)
class Step:
    phase: str
    action: Action | None = None
    value: float | None = None
    complete: bool = False
    reason: str = ""


class TransitionPlanner:
    def __init__(self, config: Config, *, max_age: float,
                 timeout: float, ac_target: float, manual_fan: bool = False,
                 combined_cool_target: bool = False):
        if not all(isfinite(v) for v in (max_age, timeout, ac_target)):
            raise ValueError("planner settings must be finite")
        if max_age <= 0 or timeout <= 0:
            raise ValueError("max age and timeout must be positive")
        self.manual_fan = manual_fan
        self.combined_cool_target = combined_cool_target
        self.config, self.max_age = config, max_age
        self.timeout, self.ac_target = timeout, ac_target
        self.target: Mode | None = None
        self.pending: Action | None = None
        self.sent_at: float | None = None
        self.wait_started: float | None = None
        self.error: str | None = None
        self.last_now: float | None = None

    def fail(self, reason: str) -> None:
        """An error is latched: fresh reconciliation requires a new planner."""
        self.error = reason

    def _confirmed(self, snapshot: Snapshot, action: Action, since: float) -> bool:
        sample, value = {
            Action.FAN_OFF: (snapshot.vent_output, False),
            Action.FAN_ON: (snapshot.vent_output, True),
            Action.AC_OFF: (snapshot.ac_mode, "off"),
            Action.AC_COOL: (snapshot.ac_mode, "cool"),
            Action.VENT_ENABLE: (snapshot.vent_enabled, True),
            Action.VENT_DISABLE: (snapshot.vent_enabled, False),
            Action.AC_SET_TARGET: (snapshot.ac_target, self.ac_target),
        }[action]
        if sample.measured_at <= since or not sample.fresh(snapshot.now, self.max_age):
            return False
        if action is Action.AC_SET_TARGET:
            actual = sample.temperature(snapshot.now, self.max_age)
            return actual is not None and abs(actual - value) < 0.01
        return snapshot.proved(sample, value, self.max_age)

    def _issue(self, snapshot: Snapshot, action: Action) -> Step:
        self.pending, self.sent_at = action, snapshot.now
        self.wait_started = None
        return Step("COMMAND_INTENT", action,
                    self.ac_target if action is Action.AC_SET_TARGET else None)

    def advance(self, snapshot: Snapshot, target: Mode, *, automatic: bool,
                ready: bool, timing_allowed: bool) -> Step:
        if not automatic:
            if self.pending is not None:
                self.fail("MANUAL_WITH_UNRESOLVED_INTENT")
            return Step("PAUSED_MANUAL")
        if self.error:
            return Step("BLOCKED", reason=self.error)
        if not ready:
            return Step("RECONCILING")
        if self.last_now is not None and snapshot.now < self.last_now:
            self.fail("CLOCK_MOVED_BACKWARDS")
            return Step("BLOCKED", reason=self.error)
        self.last_now = snapshot.now
        if self.target is not None and target is not self.target:
            self.fail("TARGET_CHANGED_RECONCILE")
            return Step("BLOCKED", reason=self.error)
        self.target = target
        if self.pending is not None:
            if self._confirmed(snapshot, self.pending, self.sent_at):
                self.pending = None
            elif snapshot.now - self.sent_at >= self.timeout:
                self.fail("READBACK_TIMEOUT")
                return Step("BLOCKED", reason=self.error)
            else:
                return Step("WAIT_READBACK", reason=self.pending.value)
        if not timing_allowed:
            return Step("WAIT_TIMING")
        if target is Mode.NO_COOLING_AVAILABLE:
            return Step("BLOCKED", reason="NO_COOLING_IS_NOT_AN_OFF_COMMAND")
        if target not in (Mode.IDLE, Mode.VENTILATION,
                          Mode.EMERGENCY_VENTILATION, Mode.AIR_CONDITIONING):
            return Step("BLOCKED", reason="UNSUPPORTED_TARGET")
        ac_off = snapshot.proved(snapshot.ac_mode, "off", self.max_age) or (
            snapshot.ac_unpowered and snapshot.power is Power.BATTERY)
        vent_disabled = snapshot.proved(snapshot.vent_enabled, False, self.max_age)
        fan_off = snapshot.proved(snapshot.vent_output, False, self.max_age)
        if target in (Mode.IDLE, Mode.VENTILATION, Mode.EMERGENCY_VENTILATION):
            if not ac_off:
                if not snapshot.ac_available:
                    return Step("BLOCKED", reason="AC_OFF_UNPROVEN")
                return self._issue(snapshot, Action.AC_OFF)
            if target is not Mode.IDLE:
                tin = snapshot.tin.temperature(snapshot.now, self.max_age)
                tout = snapshot.tout.temperature(snapshot.now, self.max_age)
                if tin is None or tout is None:
                    return Step("BLOCKED", reason="INPUT_QUALITY")
                if snapshot.power is not Power.MAINS and not (
                        target is Mode.EMERGENCY_VENTILATION and snapshot.power is Power.BATTERY
                        and self.config.emergency_on is not None):
                    return Step("BLOCKED", reason="POWER_POLICY_REQUIRED")
                if not snapshot.vent_available or snapshot.vent_ineffective:
                    return Step("BLOCKED", reason="VENT_UNAVAILABLE")
                if target is Mode.EMERGENCY_VENTILATION:
                    if self.config.emergency_delta is None:
                        return Step("BLOCKED", reason="EMERGENCY_DELTA_UNSET")
                    if (tout >= self.config.emergency_outdoor_max or tin <= tout
                            or tin - tout < self.config.emergency_delta):
                        return Step("BLOCKED", reason="OUTDOOR_NOT_USEFUL")
                elif tout >= self.config.vent_to_ac:
                    return Step("BLOCKED", reason="NORMAL_VENT_TEMPERATURE")
                if target is Mode.EMERGENCY_VENTILATION and self.manual_fan:
                    if not vent_disabled:
                        return self._issue(snapshot, Action.VENT_DISABLE)
                    if not snapshot.proved(snapshot.vent_output, True, self.max_age):
                        return self._issue(snapshot, Action.FAN_ON)
                    return Step("COMPLETE", complete=True)
                if snapshot.proved(snapshot.vent_enabled, True, self.max_age):
                    return Step("COMPLETE", complete=True)
                return self._issue(snapshot, Action.VENT_ENABLE)
        if not vent_disabled:
            if not snapshot.vent_available:
                return Step("BLOCKED", reason="VENT_DISABLE_UNPROVEN")
            return self._issue(snapshot, Action.VENT_DISABLE)
        if not fan_off:
            if self.manual_fan:
                if not snapshot.vent_output.fresh(snapshot.now, self.max_age):
                    return Step("BLOCKED", reason="VENT_OUTPUT_UNPROVEN")
                return self._issue(snapshot, Action.FAN_OFF)
            if self.wait_started is None:
                self.wait_started = snapshot.now
            if snapshot.now - self.wait_started >= self.timeout:
                self.fail("VENT_OUTPUT_TIMEOUT")
                return Step("BLOCKED", reason=self.error)
            return Step("WAIT_VENT_OUTPUT_OFF")
        self.wait_started = None
        if target is Mode.IDLE:
            return Step("COMPLETE", complete=True)
        # Recheck every launch guard after all awaited readbacks.
        tin = snapshot.tin.temperature(snapshot.now, self.max_age)
        tout = snapshot.tout.temperature(snapshot.now, self.max_age)
        if tin is None or tout is None:
            return Step("BLOCKED", reason="INPUT_QUALITY")
        if tout <= self.config.ac_hard_min:
            return Step("BLOCKED", reason="AC_HARD_MIN_BLOCK")
        if snapshot.power is not Power.MAINS:
            return Step("BLOCKED", reason="AC_REQUIRES_MAINS")
        if not snapshot.ac_available or not snapshot.ac_fault_allows_operation or snapshot.ac_ineffective:
            return Step("BLOCKED", reason="AC_UNAVAILABLE")
        if (tout < 0 and not snapshot.vent_ineffective and snapshot.vent_available
                and not snapshot.proved(snapshot.ac_mode, "cool", self.max_age)):
            return Step("BLOCKED", reason="NEGATIVE_TEMPERATURE_RESERVE_ONLY")
        actual = snapshot.ac_target.temperature(snapshot.now, self.max_age)
        if actual is None or abs(actual - self.ac_target) >= 0.01:
            # Combined mode/target dispatch is allowed only after all launch guards.
            if not ac_off and not self.combined_cool_target:
                return Step("BLOCKED", reason="TARGET_UPDATE_PROFILE_REQUIRED")
            return self._issue(snapshot, Action.AC_SET_TARGET)
        if not snapshot.proved(snapshot.ac_mode, "cool", self.max_age):
            return self._issue(snapshot, Action.AC_COOL)
        return Step("COMPLETE", complete=True)
