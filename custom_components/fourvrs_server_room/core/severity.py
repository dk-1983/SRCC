"""Critical temperature latch, independent of the ventilation trend window."""
from dataclasses import dataclass
from math import isfinite

from .health import slope
from .models import Config, Sample, Severity


@dataclass(frozen=True)
class Alarm:
    severity: Severity
    thermal_critical: bool
    reason: str
    recovery_elapsed: float = 0.0
    degraded: bool = False


class TemperatureAlarm:
    def __init__(self, config: Config, *, max_age: float, max_gap: float):
        if not all(isfinite(v) and v > 0 for v in (max_age, max_gap)):
            raise ValueError("freshness and continuity gap must be positive")
        self.config, self.max_age, self.max_gap = config, max_age, max_gap
        self.critical = False
        self.source: str | None = None
        self.points: list[tuple[float, float]] = []
        self.last_call: float | None = None

    def update(self, sample: Sample[float], *, now: float,
               other_critical: bool = False) -> Alarm:
        value = sample.temperature(now, self.max_age)
        clock_reset = self.last_call is not None and now < self.last_call
        self.last_call = now
        if self.source != sample.source or clock_reset:
            self.points.clear()
            self.source = sample.source
        if value is None:
            self.points.clear()
            return Alarm(Severity.CRITICAL if self.critical or other_critical
                         else Severity.WARNING, self.critical, "TEMPERATURE_INVALID",
                         degraded=True)
        if value >= self.config.critical_on:
            self.critical = True
            self.points.clear()
        elapsed = 0.0
        if self.critical and value <= self.config.critical_recovery:
            if self.points and (sample.measured_at - self.points[-1][0] > self.max_gap
                                or sample.measured_at < self.points[-1][0]):
                self.points.clear()
            if not self.points or sample.measured_at > self.points[-1][0]:
                self.points.append((sample.measured_at, value))
            # Keep a bounded rolling recovery window; all received values must
            # stay at/below the threshold, including values between minute ticks.
            while (len(self.points) > 3
                   and self.points[-1][0] - self.points[1][0] >= self.config.recovery_hold):
                self.points.pop(0)
            elapsed = self.points[-1][0] - self.points[0][0]
            if (len(self.points) >= 3 and elapsed >= self.config.recovery_hold
                    and slope(self.points) < 0
                    and self.points[0][1] - self.points[-1][1]
                    > self.config.minimum_recovery_drop):
                self.critical = False
                self.points.clear()
        else:
            self.points.clear()
        if self.critical or other_critical:
            return Alarm(Severity.CRITICAL, self.critical,
                         "THERMAL_CRITICAL" if self.critical else "OTHER_CRITICAL",
                         elapsed)
        if self.config.warning_on is not None and value >= self.config.warning_on:
            return Alarm(Severity.WARNING, False, "TEMPERATURE_WARNING")
        return Alarm(Severity.NORMAL, False, "TEMPERATURE_NORMAL")
