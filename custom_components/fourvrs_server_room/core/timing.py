"""Continuous candidate hold and confirmed-mode dwell, using monotonic time."""
from dataclasses import dataclass
from math import isfinite

from .models import Mode


@dataclass(frozen=True)
class Timing:
    allowed: bool
    condition_remaining: float
    dwell_remaining: float


class TimingGate:
    def __init__(self, *, condition_hold: float, minimum_dwell: float):
        if not all(isfinite(value) and value >= 0 for value in (condition_hold, minimum_dwell)):
            raise ValueError("timing durations must be finite and nonnegative")
        self.hold, self.dwell = condition_hold, minimum_dwell
        self.candidate: Mode | None = None
        self.since: float | None = None
        self.committed_at: float | None = None
        self.last_now: float | None = None

    def confirmed(self, *, now: float) -> None:
        """Only the confirmed end of a transition starts mode dwell."""
        if not isfinite(now):
            raise ValueError("time must be finite")
        self.committed_at = now
        self.candidate, self.since = None, None

    def update(self, candidate: Mode | None, *, now: float,
               urgent: bool = False) -> Timing:
        if not isfinite(now):
            raise ValueError("time must be finite")
        if self.last_now is not None and now < self.last_now:
            self.candidate, self.since = None, None
            self.committed_at = now
        self.last_now = now
        if candidate is None:
            self.candidate, self.since = None, None
            return Timing(False, self.hold, 0.0)
        if candidate is not self.candidate:
            self.candidate, self.since = candidate, now
        hold_left = max(0.0, self.hold - (now - self.since))
        dwell_left = (max(0.0, self.dwell - (now - self.committed_at))
                      if self.committed_at is not None else 0.0)
        # Urgency bypasses optimization timers, never readback or hard guards.
        return Timing(urgent or (hold_left == 0 and dwell_left == 0), hold_left, dwell_left)
