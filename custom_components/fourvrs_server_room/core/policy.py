"""Cooling strategy proposes goals; it cannot issue equipment commands."""
from .models import Config, Decision, Mode, Power, Snapshot

VENT_MODES = (Mode.VENTILATION, Mode.EMERGENCY_VENTILATION)


def choose(snapshot: Snapshot, config: Config, *, max_age: float,
           cooling_required: bool | None = None) -> Decision:
    tin = snapshot.tin.temperature(snapshot.now, max_age)
    tout = snapshot.tout.temperature(snapshot.now, max_age)
    if tin is None or tout is None:
        return Decision(None, "TEMPERATURE_UNTRUSTED", ("INPUT_QUALITY",))
    urgent = snapshot.thermal_critical or tin >= config.critical_on
    if snapshot.power is Power.BATTERY and config.emergency_on is not None:
        if not snapshot.vent_available:
            return Decision(Mode.NO_COOLING_AVAILABLE, "VENT_UNAVAILABLE", urgent=urgent)
        if cooling_required is None:
            return Decision(None, "EMERGENCY_DEMAND_UNSET", urgent=urgent)
        if not cooling_required and not urgent:
            return Decision(Mode.IDLE, "NO_EMERGENCY_DEMAND")
        if config.emergency_delta is None:
            return Decision(None, "EMERGENCY_DELTA_UNSET", urgent=urgent)
        if tout >= config.emergency_outdoor_max or tin <= tout or tin - tout < config.emergency_delta:
            return Decision(Mode.IDLE, "OUTDOOR_NOT_USEFUL", urgent=urgent)
        return Decision(Mode.EMERGENCY_VENTILATION, "BATTERY_VENTILATION", urgent=urgent)
    if snapshot.power is not Power.MAINS:
        # Battery thresholds and load budget are not agreed. Never assume mains
        # or issue a new cooling goal based solely on On Battery.
        return Decision(None, "POWER_POLICY_REQUIRED", (snapshot.power.value,), urgent)
    ac_ok = (tout >= config.ac_hard_min and snapshot.ac_available
             and snapshot.ac_fault_allows_operation and not snapshot.ac_ineffective)
    vent_ok = snapshot.vent_available and not snapshot.vent_ineffective
    # Thermal recovery takes priority over seasonal economy. This does not
    # label the fan as broken or discard it when AC cannot run.
    if urgent and ac_ok:
        return Decision(Mode.AIR_CONDITIONING, "THERMAL_RESERVE_AC", urgent=True)
    if snapshot.reserve_hold and ac_ok:
        return Decision(Mode.AIR_CONDITIONING, "VENT_RETRY_LOCKED",
                        urgent=config.warning_on is not None and tin >= config.warning_on)
    if snapshot.current is Mode.AIR_CONDITIONING and ac_ok:
        if tout <= config.ac_to_vent and vent_ok:
            return Decision(Mode.VENTILATION, "OUTDOOR_VENT_THRESHOLD", urgent=urgent)
        return Decision(Mode.AIR_CONDITIONING, "HOLD_AC", urgent=urgent)
    if (snapshot.current is None and config.ac_to_vent < tout < config.vent_to_ac
            and vent_ok and ac_ok):
        return Decision(Mode.VENTILATION, "INITIAL_VENT_SELECTED", urgent=urgent)
    if vent_ok and tout < config.vent_to_ac:
        # Keep usable ventilation when AC cannot run; thermal urgency is
        # not evidence that the fan itself has failed.
        return Decision(Mode.VENTILATION, "OUTDOOR_VENT_SELECTED", urgent=urgent)
    if ac_ok:
        if tout >= config.vent_to_ac or snapshot.vent_ineffective or not snapshot.vent_available:
            return Decision(Mode.AIR_CONDITIONING, "AC_PRIMARY_OR_RESERVE", urgent=urgent)
        # Uncertain initial selection within the positive dead band.
        return Decision(None, "INITIAL_MODE_POLICY_REQUIRED", urgent=urgent)
    if not vent_ok:
        return Decision(Mode.NO_COOLING_AVAILABLE, "NO_HEALTHY_COOLING", urgent=urgent)
    if cooling_required is None and not urgent:
        return Decision(None, "EMERGENCY_DEMAND_UNSET", urgent=urgent)
    if cooling_required is False and not urgent:
        return Decision(Mode.IDLE, "NO_EMERGENCY_DEMAND")
    if config.emergency_delta is None:
        return Decision(None, "EMERGENCY_DELTA_UNSET", urgent=urgent)
    if tout >= config.emergency_outdoor_max or tin <= tout:
        return Decision(Mode.IDLE, "OUTDOOR_NOT_USEFUL", urgent=urgent)
    if tin - tout >= config.emergency_delta:
        return Decision(Mode.EMERGENCY_VENTILATION, "AC_UNAVAILABLE_VENT_USEFUL", urgent=urgent)
    return Decision(Mode.NO_COOLING_AVAILABLE, "OUTDOOR_NOT_USEFUL", urgent=urgent)
