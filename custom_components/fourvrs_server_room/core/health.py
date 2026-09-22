"""Temperature trends using actual sample times, not event counts."""
from dataclasses import dataclass
from math import isfinite

from .models import Config, Sample


def slope(points: list[tuple[float, float]]) -> float:
    """Least squares slope in degrees per minute."""
    if len(points) < 2:
        return 0.0
    origin = points[0][0]
    xs = [(time - origin) / 60.0 for time, _ in points]
    ys = [value for _, value in points]
    mean_x, mean_y = sum(xs) / len(xs), sum(ys) / len(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    return (sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            / denominator) if denominator else 0.0


@dataclass(frozen=True)
class Trend:
    ready: bool = False
    rate_per_minute: float | None = None
    change: float | None = None
    ineffective: bool | None = None
    samples: int = 0
    reason: str = "COLLECTING"


class VentilationTrend:
    """First result at t0+300s, from five distinct fresh measurements.

    Caller supplies confirmed continuous active output on every update.
    Sampling tolerance is explicit; delayed callbacks cannot backfill points.
    """
    def __init__(self, config: Config, *, max_age: float, tolerance: float):
        if (not isfinite(max_age) or max_age <= 0 or not isfinite(tolerance)
                or not 0 <= tolerance < config.sample_interval):
            raise ValueError("invalid freshness or sampling tolerance")
        self.config, self.max_age, self.tolerance = config, max_age, tolerance
        self.reset()

    def reset(self) -> None:
        self.started: float | None = None
        self.next_due: float | None = None
        self.source: str | None = None
        self.last_measurement: float | None = None
        self.last_call: float | None = None
        self.points: list[tuple[float, float]] = []

    def update(self, sample: Sample[float], *, now: float, active: bool) -> Trend:
        value = sample.temperature(now, self.max_age)
        if (not active or value is None
                or (self.last_call is not None and now < self.last_call)):
            self.reset()
            return Trend(reason="INACTIVE_OR_INVALID")
        if self.source != sample.source or self.started is None:
            self.reset()
            self.source, self.started = sample.source, now
            self.next_due = now + self.config.sample_interval
            self.last_measurement = sample.measured_at
        self.last_call = now
        if now < self.next_due:
            return self._result()
        if (now > self.next_due + self.tolerance
                or sample.measured_at <= self.last_measurement
                or sample.measured_at < self.next_due - self.tolerance):
            self.reset()
            return Trend(reason="MISSED_OR_DUPLICATE_SAMPLE")
        self.points.append((sample.measured_at, value))
        self.points = self.points[-self.config.vent_samples:]
        self.last_measurement = sample.measured_at
        self.next_due += self.config.sample_interval
        return self._result()

    def _result(self) -> Trend:
        if len(self.points) < self.config.vent_samples:
            return Trend(samples=len(self.points))
        rate = slope(self.points)
        change = self.points[-1][1] - self.points[0][1]
        threshold = self.config.minimum_vent_rise
        ineffective = None if threshold is None else rate > 0 and change >= threshold
        return Trend(True, rate, change, ineffective, len(self.points),
                     "RISE_THRESHOLD_UNSET" if threshold is None else "EVALUATED")
