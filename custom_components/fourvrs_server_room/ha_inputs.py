"""Read HA states, normalize units and retain source publication age."""
from datetime import datetime
from math import isfinite

from .core.models import Mode, Power, Quality, Sample, Snapshot


def read_sample(state, *, source: str, now: float, wall_now: datetime,
                kind: str, attribute: str | None = None,
                clock_anchor: tuple[float, datetime] | None = None) -> Sample:
    if state is None:
        return Sample(None, source, now, Quality.UNAVAILABLE)
    if state.attributes.get("restored"):
        return Sample(None, source, now, Quality.UNVERIFIED)
    if state.state in ("unknown", "unavailable"):
        return Sample(None, source, now, Quality(state.state))
    reported = getattr(state, "last_reported", None)
    if reported is None or reported.tzinfo is None:
        return Sample(None, source, now, Quality.UNVERIFIED)
    age = (wall_now - reported).total_seconds()
    if age < 0:
        return Sample(None, source, now, Quality.INVALID)
    measured = (clock_anchor[0] + (reported - clock_anchor[1]).total_seconds()
                if clock_anchor is not None else now - age)
    value = state.attributes.get(attribute) if attribute else state.state
    try:
        if kind == "temperature":
            if isinstance(value, bool):
                raise ValueError("boolean temperature")
            value = float(value)
            unit = state.attributes.get("temperature_unit" if attribute else "unit_of_measurement")
            if unit == "°F":
                value = (value - 32) * 5 / 9
            elif unit == "K":
                value -= 273.15
            elif unit != "°C":
                raise ValueError("unknown unit")
            if not isfinite(value):
                raise ValueError("nonfinite temperature")
        elif kind == "bool":
            mapping = {"on": True, "off": False, "1": True, "0": False,
                       "1.0": True, "0.0": False}
            if not isinstance(value, str) or value not in mapping:
                raise ValueError("unknown boolean representation")
            value = mapping[value]
        elif kind == "mode":
            if value not in ("off", "cool", "heat", "auto", "heat_cool", "dry", "fan_only"):
                raise ValueError("unknown HVAC mode")
        else:
            raise ValueError("unsupported role")
    except (TypeError, ValueError, OverflowError):
        return Sample(None, source, measured, Quality.INVALID)
    return Sample(value, source, measured)


def power_state(online: Sample, battery: Sample, now: float, max_age: float) -> Power:
    if not battery.fresh(now, max_age):
        return Power.UNKNOWN
    if battery.value is True:
        # Online may mean connectivity in an upstream integration. The configuration
        # must map a mains-online signal; on-battery remains authoritative.
        return Power.BATTERY
    if online.fresh(now, max_age) and online.value is True and battery.value is False:
        return Power.MAINS
    return Power.UNKNOWN


def reported_mode(snapshot: Snapshot, max_age: float) -> str:
    ac_off = snapshot.proved(snapshot.ac_mode, "off", max_age)
    ac_cool = snapshot.proved(snapshot.ac_mode, "cool", max_age)
    vent_on = snapshot.proved(snapshot.vent_enabled, True, max_age)
    vent_off = snapshot.proved(snapshot.vent_enabled, False, max_age)
    output_off = snapshot.proved(snapshot.vent_output, False, max_age)
    if not ac_off and snapshot.ac_mode.fresh(snapshot.now, max_age) and vent_on:
        return "conflict"
    if ac_off and vent_on:
        return Mode.VENTILATION.value
    if ac_cool and vent_off and output_off:
        return Mode.AIR_CONDITIONING.value
    if ac_off and vent_off and output_off:
        return Mode.IDLE.value
    return "unknown"
