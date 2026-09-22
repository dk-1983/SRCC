"""Primary temperature with zero, one or two configured backup sources."""
from dataclasses import dataclass
from enum import Enum
from math import isfinite

from .models import Sample


class Fallback(str, Enum):
    DISABLED = "disabled"
    MEAN_REQUIRE_BOTH = "mean_require_both"
    MEAN_ALLOW_ONE = "mean_allow_one"


@dataclass(frozen=True)
class TemperatureSelection:
    sample: Sample[float] | None
    degraded: bool
    reason: str
    warning_sources: tuple[str, ...] = ()


def select_temperature(primary: Sample[float], backups: tuple[Sample[float], ...],
                       *, now: float, max_age: float,
                       fallback: Fallback = Fallback.DISABLED,
                       max_skew: float | None = None) -> TemperatureSelection:
    if primary.temperature(now, max_age) is not None:
        return TemperatureSelection(primary, False, "TRM_PRIMARY")
    if fallback is Fallback.DISABLED or not backups:
        return TemperatureSelection(None, True, "FALLBACK_NOT_CONFIGURED")
    if (len(backups) not in (1, 2) or len({item.source for item in backups}) != len(backups)
            or primary.source in {item.source for item in backups}):
        return TemperatureSelection(None, True, "TWO_DISTINCT_BACKUP_ROLES_REQUIRED")
    values = [item.temperature(now, max_age) for item in backups]
    valid = [item for item, value in zip(backups, values) if value is not None]
    missing = tuple(item.source for item, value in zip(backups, values) if value is None)
    if not valid:
        return TemperatureSelection(None, True, "BACKUP_UNAVAILABLE", missing)
    if len(valid) == 1:
        if fallback is Fallback.MEAN_REQUIRE_BOTH:
            return TemperatureSelection(None, True, "BOTH_BACKUPS_REQUIRED", missing)
        return TemperatureSelection(valid[0], True, "BOLID_SINGLE_FALLBACK", missing)
    if max_skew is None or not isfinite(max_skew) or max_skew < 0:
        return TemperatureSelection(None, True, "BACKUP_SKEW_POLICY_REQUIRED")
    times = [item.measured_at for item in backups]
    if max(times) - min(times) > max_skew:
        return TemperatureSelection(None, True, "BACKUP_TIMES_DIVERGE")
    # Use the older timestamp: evaluating a mean never refreshes its inputs.
    source = "bolid_mean:" + "+".join(sorted(item.source for item in backups))
    mean = Sample(sum(values) / 2.0, source, min(times))
    return TemperatureSelection(mean, True, "BOLID_MEAN_FALLBACK")
