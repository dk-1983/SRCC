"""Immutable inputs and explicit configuration for the decision core."""
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Generic, TypeVar

T = TypeVar("T")


class Quality(str, Enum):
    VALID = "valid"
    UNKNOWN = "unknown"
    UNAVAILABLE = "unavailable"
    STALE = "stale"
    INVALID = "invalid"
    UNVERIFIED = "unverified"


class Mode(str, Enum):
    IDLE = "idle"
    VENTILATION = "ventilation"
    AIR_CONDITIONING = "air_conditioning"
    EMERGENCY_VENTILATION = "emergency_ventilation"
    NO_COOLING_AVAILABLE = "no_cooling_available"


class Severity(str, Enum):
    NORMAL = "normal"
    WARNING = "warning"
    CRITICAL = "critical"


class Power(str, Enum):
    MAINS = "mains"
    BATTERY = "battery"
    UNKNOWN = "unknown"
    CONFLICT = "conflict"


@dataclass(frozen=True)
class Sample(Generic[T]):
    value: T | None
    source: str
    measured_at: float
    quality: Quality = Quality.VALID

    # Optional source-specific limit; timestamps remain unchanged for readback.
    freshness_limit: float | None = None

    def fresh(self, now: float, max_age: float) -> bool:
        max_age = self.freshness_limit if self.freshness_limit is not None else max_age
        return (
            self.quality is Quality.VALID
            and self.value is not None
            and isfinite(now)
            and isfinite(self.measured_at)
            and isfinite(max_age)
            and max_age > 0
            and 0 <= now - self.measured_at <= max_age
        )

    def temperature(self, now: float, max_age: float) -> float | None:
        if not self.fresh(now, max_age) or isinstance(self.value, bool):
            return None
        if not isinstance(self.value, (float, int)) or not isfinite(self.value):
            return None
        return float(self.value)


@dataclass(frozen=True)
class Config:
    ac_hard_min: float = -5.0
    vent_to_ac: float = 1.0
    ac_to_vent: float = -1.0
    critical_on: float = 28.0
    critical_recovery: float = 27.0
    recovery_hold: float = 120.0
    sample_interval: float = 60.0
    vent_observation: float = 300.0
    vent_samples: int = 5
    warning_on: float | None = 26.0
    emergency_delta: float | None = None
    minimum_vent_rise: float | None = None
    emergency_on: float | None = None
    emergency_off: float | None = None
    emergency_outdoor_max: float = 10.0
    # Direction only is agreed; a nonzero noise margin can be configured.
    minimum_recovery_drop: float = 0.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (float, int))
                or not isfinite(value)
            ):
                raise ValueError(f"{name} must be finite")
        if not self.ac_hard_min < self.ac_to_vent < self.vent_to_ac:
            raise ValueError("hard minimum < AC-to-vent < vent-to-AC required")
        if self.vent_to_ac < 0:
            raise ValueError("normal AC starts below zero are not supported")
        if self.critical_recovery >= self.critical_on:
            raise ValueError("critical recovery must be below critical entry")
        if self.warning_on is not None and self.warning_on >= self.critical_on:
            raise ValueError("warning must be below critical")
        if self.recovery_hold <= 0 or self.sample_interval <= 0:
            raise ValueError("durations must be positive")
        if not isinstance(self.vent_samples, int) or self.vent_samples < 3:
            raise ValueError("at least three samples required")
        if self.vent_observation != self.sample_interval * self.vent_samples:
            raise ValueError("observation must equal interval times sample count")
        if self.emergency_delta is not None and self.emergency_delta < 0:
            raise ValueError("emergency delta must be nonnegative")
        if self.minimum_vent_rise is not None and self.minimum_vent_rise <= 0:
            raise ValueError("minimum ventilation rise must be positive")
        if (self.emergency_on is None) != (self.emergency_off is None):
            raise ValueError("both emergency thresholds required")
        if self.emergency_on is not None and self.emergency_off >= self.emergency_on:
            raise ValueError("emergency off must be below on")
        if self.minimum_recovery_drop < 0:
            raise ValueError("recovery drop must not be negative")


@dataclass(frozen=True)
class Decision:
    desired: Mode | None
    reason: str
    blockers: tuple[str, ...] = ()
    urgent: bool = False


@dataclass(frozen=True)
class Snapshot:
    now: float
    tin: Sample[float]
    tout: Sample[float]
    power: Power
    ac_available: bool
    vent_available: bool
    vent_enabled: Sample[bool]
    vent_output: Sample[bool]
    ac_mode: Sample[str]
    ac_target: Sample[float]
    ac_fault: bool | None = None
    vent_ineffective: bool = False
    ac_ineffective: bool = False
    thermal_critical: bool = False
    current: Mode | None = None

    # Unassigned optional fault input differs from a configured input with lost data.
    ac_fault_monitored: bool = True
    ac_unpowered: bool = False
    # None is legacy HA publication readback; False blocks telemetry confirmation.
    ac_command_confirmed: bool | None = None

    @property
    def ac_fault_allows_operation(self) -> bool:
        return self.ac_fault is False or (not self.ac_fault_monitored and self.ac_fault is None)

    def proved(self, sample: Sample, value: object, max_age: float) -> bool:
        # Never accept a number/string as a boolean readback.
        return (sample.fresh(self.now, max_age)
                and type(sample.value) is type(value) and sample.value == value)
