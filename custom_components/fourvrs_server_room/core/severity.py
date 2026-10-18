"""Critical rack latch with measured AC setpoint recovery."""
from dataclasses import dataclass
from math import isfinite
from .models import Config, Sample, Severity


@dataclass(frozen=True)
class Alarm:
    severity: Severity
    thermal_critical: bool
    reason: str
    degraded: bool = False


class TemperatureAlarm:
    def __init__(self, config: Config, *, max_age: float, max_gap: float):
        if not all(isfinite(v) and v > 0 for v in (max_age, max_gap)):
            raise ValueError("freshness limits must be positive")
        self.config, self.max_age = config, max_age
        self.critical = False
        self.entered_at = None

    def update(self, sample: Sample[float], *, now: float,
               other_critical: bool = False, ac_temperature: Sample[float] | None = None,
               ac_target: float | None = None) -> Alarm:
        value = sample.temperature(now, self.max_age)
        if value is None:
            return Alarm(Severity.CRITICAL if self.critical or other_critical
                         else Severity.WARNING, self.critical, "TEMPERATURE_INVALID",
                         degraded=True)
        if value >= self.config.critical_on:
            self.critical = True
            self.entered_at = now
        actual = ac_temperature.temperature(now, self.max_age) if ac_temperature else None
        recovered = (self.critical and value <= self.config.critical_recovery
                     and actual is not None and ac_target is not None
                     and isfinite(ac_target) and actual <= ac_target
                     and self.entered_at is not None
                     and sample.measured_at > self.entered_at
                     and ac_temperature.measured_at > self.entered_at)
        if recovered:
            self.critical = False
            self.entered_at = None
        if self.critical or other_critical:
            return Alarm(Severity.CRITICAL, self.critical,
                         "THERMAL_CRITICAL" if self.critical else "OTHER_CRITICAL")
        if self.config.warning_on is not None and value >= self.config.warning_on:
            return Alarm(Severity.WARNING, False, "TEMPERATURE_WARNING")
        return Alarm(Severity.NORMAL, False, "TEMPERATURE_NORMAL")
