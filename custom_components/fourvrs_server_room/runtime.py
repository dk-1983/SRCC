"""Home Assistant read-only runtime and callback cleanup."""
from datetime import timedelta
from time import monotonic

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .configuration import core_config
from .const import ENTITY_ROLES
from .core.controller import Observer
from .core.models import Mode, Quality, Sample, Snapshot
from .ha_inputs import power_state, read_sample, reported_mode


class Runtime:
    def __init__(self, hass, entry, data):
        self.hass, self.entry, self.data = hass, entry, data
        self._clock_anchor = monotonic()
        self._wall_anchor = dt_util.utcnow()
        self.observer = Observer(core_config(data), max_age=data["max_age"],
                                 recovery_max_gap=data["recovery_max_gap"],
                                 sample_tolerance=data["sample_tolerance"],
                                 backup_max_skew=data["backup_max_skew"])
        self.status = None
        self.snapshot = None
        self.reported_mode = "unknown"
        self.listeners = set()
        self.unsubscribers = []
        self.stopped = False
        self.backups = ()
        self.next_backup = 0.0

    @callback
    def start(self):
        entity_ids = [self.data[key] for key in ENTITY_ROLES if key in self.data]
        self.unsubscribers.append(async_track_state_change_event(self.hass, entity_ids, self._changed))
        # Watchdog has no device polling. It notices age expiry without a state
        # change and serves minute-based sampling even for unchanged values.
        self.unsubscribers.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=1)))
        self.refresh()

    @callback
    def stop(self):
        self.stopped = True
        for cancel in self.unsubscribers:
            cancel()
        self.unsubscribers.clear()
        self.listeners.clear()

    @callback
    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    @callback
    def _changed(self, event):
        # Loss of a backup is handled immediately. A failed backup that recovers
        # is sampled with its pair at the next scheduled minute.
        self.refresh()

    @callback
    def _tick(self, _now):
        self.refresh()

    @callback
    def refresh(self):
        if self.stopped:
            return
        now = monotonic()
        wall_now = self._wall_anchor + timedelta(seconds=now - self._clock_anchor)
        age = self.data["max_age"]

        def read(role, kind, attribute=None):
            entity_id = self.data.get(role)
            return read_sample(self.hass.states.get(entity_id) if entity_id else None,
                               source=entity_id or role, now=now, wall_now=wall_now,
                               kind=kind, attribute=attribute,
                               clock_anchor=(self._clock_anchor, self._wall_anchor))

        backup_roles = ("bolid_temperature_1", "bolid_temperature_2")
        if all(key in self.data for key in backup_roles):
            if now >= self.next_backup:
                self.backups = tuple(read(key, "temperature") for key in backup_roles)
                self.next_backup = now + 60
            else:
                # Invalidate a cached backup immediately if HA reports its loss.
                for index, role in enumerate(backup_roles):
                    current = read(role, "temperature")
                    if current.quality is not Quality.VALID:
                        values = list(self.backups)
                        values[index] = current
                        self.backups = tuple(values)
        enabled, output = read("vent_enable", "bool"), read("vent_output", "bool")
        mode = read("climate", "mode")
        fault = read("ac_fault", "bool")
        climate_state = self.hass.states.get(self.data.get("climate", "climate.srcc_unassigned"))
        # Climate setpoints use HA's system temperature unit, not a presumed °C.
        if climate_state is not None:
            from types import SimpleNamespace
            attributes = dict(climate_state.attributes)
            attributes["temperature_unit"] = self.hass.config.units.temperature_unit
            proxy = SimpleNamespace(state=climate_state.state, attributes=attributes,
                                    last_reported=climate_state.last_reported)
            target = read_sample(proxy, source=climate_state.entity_id + ":target", now=now,
                                 wall_now=wall_now, kind="temperature", attribute="temperature",
                                 clock_anchor=(self._clock_anchor, self._wall_anchor))
        else:
            target = Sample(None, "ac_target", now, Quality.UNAVAILABLE)
        state = Snapshot(
            now=now, tin=read("trm_temperature", "temperature"),
            tout=read("outdoor_temperature", "temperature"),
            power=power_state(read("ups_online", "bool"), read("ups_on_battery", "bool"), now, age),
            ac_available=mode.fresh(now, age),
            vent_available=enabled.fresh(now, age) and output.fresh(now, age),
            vent_enabled=enabled, vent_output=output, ac_mode=mode, ac_target=target,
            ac_fault=fault.value if fault.fresh(now, age) else None,
        )
        from dataclasses import replace
        self.reported_mode = reported_mode(state, age)
        current = Mode(self.reported_mode) if self.reported_mode not in ("unknown", "conflict") else None
        state = replace(state, current=current)
        self.snapshot = state
        self.status = self.observer.evaluate(state, backups=self.backups,
                                             other_critical=self.reported_mode == "conflict")
        for listener in tuple(self.listeners):
            listener()
