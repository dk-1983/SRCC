"""Read-only orchestration of source selection, alarms, trends and decisions."""
from dataclasses import dataclass, replace

from .health import Trend, VentilationTrend
from .inputs import Fallback, TemperatureSelection, select_temperature
from .models import Config, Decision, Quality, Sample, Severity, Snapshot
from .policy import choose
from .severity import Alarm, TemperatureAlarm


@dataclass(frozen=True)
class Status:
    temperature: TemperatureSelection
    alarm: Alarm
    trend: Trend
    decision: Decision
    degraded: bool
    warnings: tuple[str, ...]


class Observer:
    """No command dispatch; unavailable setup policy remains an explicit blocker.

    Freshness/timing tolerances must be supplied by the input adapter's contract.
    Every timestamp is on the same monotonic clock. HA wall times need conversion
    by a future adapter; passing raw wall time and monotonic time together is invalid.
    """
    def __init__(self, config: Config, *, max_age: float, recovery_max_gap: float,
                 sample_tolerance: float, backup_max_skew: float,
                 fallback: Fallback = Fallback.MEAN_ALLOW_ONE, ac_target: float = 22.0):
        self.config, self.max_age = config, max_age
        self.ac_target = ac_target
        self.fallback, self.backup_max_skew = fallback, backup_max_skew
        self.alarm = TemperatureAlarm(config, max_age=max_age, max_gap=recovery_max_gap)
        self.trend = VentilationTrend(config, max_age=max_age, tolerance=sample_tolerance)

    def evaluate(self, snapshot: Snapshot, *, backups: tuple[Sample[float], ...] = (),
                 cooling_required: bool | None = None, other_critical: bool = False) -> Status:
        selected = select_temperature(snapshot.tin, backups, now=snapshot.now,
                                      max_age=self.max_age, fallback=self.fallback,
                                      max_skew=self.backup_max_skew)
        temperature = selected.sample or Sample(None, "no_temperature", snapshot.now, Quality.UNKNOWN)
        # Alarm evaluation always precedes trend collection, also on source change.
        alarm = self.alarm.update(temperature, now=snapshot.now, other_critical=other_critical,
                                  ac_temperature=snapshot.ac_temperature, ac_target=self.ac_target)
        active = (snapshot.vent_available
                  and snapshot.proved(snapshot.vent_enabled, True, self.max_age)
                  and snapshot.proved(snapshot.vent_output, True, self.max_age))
        trend = self.trend.update(temperature, now=snapshot.now, active=active)
        state = replace(snapshot, tin=temperature, thermal_critical=alarm.thermal_critical,
                        vent_ineffective=snapshot.vent_ineffective or trend.ineffective is True)
        decision = choose(state, self.config, max_age=self.max_age, cooling_required=cooling_required)
        warnings = tuple(f"BACKUP_UNAVAILABLE:{source}" for source in selected.warning_sources)
        if warnings and alarm.severity is Severity.NORMAL:
            alarm = replace(alarm, severity=Severity.WARNING, reason="BACKUP_UNAVAILABLE")
        if selected.degraded:
            alarm = replace(alarm, degraded=True)
        return Status(selected, alarm, trend, decision, selected.degraded or alarm.degraded, warnings)
