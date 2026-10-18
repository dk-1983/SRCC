"""Home Assistant input runtime, observation and serialized execution scheduling."""
import asyncio
from dataclasses import replace
from datetime import timedelta
from time import monotonic

from homeassistant.core import callback
from homeassistant.helpers.storage import Store
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.util import dt as dt_util

from .configuration import core_config, validate
from .const import ENTITY_ROLES
from .core.controller import Observer
from .core.models import Mode, Power, Quality, Sample, Snapshot
from .ha_inputs import power_state, read_sample, reported_mode


class Runtime:
    def __init__(self, hass, entry, data):
        self.hass, self.entry, self.data = hass, entry, data
        self.control_store = Store(hass, 1, f"fourvrs_server_room.{entry.entry_id}.control")
        self.control_lock = asyncio.Lock()
        self.reserve_store = Store(hass, 1, f"fourvrs_server_room.{entry.entry_id}.reserve")
        self._saved_reserve = False
        self.reserve_task = None
        self.reserve_lock = asyncio.Lock()
        self._clock_anchor = monotonic()
        self._wall_anchor = dt_util.utcnow()
        self.observer = Observer(core_config(data), max_age=data["max_age"],
                                 recovery_max_gap=data["recovery_max_gap"],
                                 sample_tolerance=data["sample_tolerance"],
                                 backup_max_skew=data["backup_max_skew"],
                                 ac_target=data.get("ac_target", 22.0))
        self.status = None
        self.snapshot = None
        self.reported_mode = "unknown"
        self.listeners = set()
        self.unsubscribers = []
        self.stopped = False
        self.backups = ()
        self.next_backup = 0.0
        self.demand = None
        self.vent_failed = False
        self.input_status = {}
        self.emergency_ac_target = None
        from .telemetry import Telemetry
        self.telemetry = Telemetry()
        from .execution import Executor
        self.executor = Executor(self)
        from .haier_telemetry import HaierTelemetry
        self.haier = HaierTelemetry() if data.get("ac_telemetry") else None
        self.haier_result_ready = False
        from .voice import ProcessVoice
        self.voice = ProcessVoice(self)

    @property
    def active_ac_target(self):
        return self.emergency_ac_target if self.emergency_ac_target is not None else self.data.get("ac_target", 22.0)

    def _update_emergency_target(self, state, climate_state):
        previous = self.emergency_ac_target
        config = self.observer.config
        sample = self.status.temperature.sample
        rack = sample.temperature(state.now, self.data["max_age"]) if sample else None
        recovery = min(config.critical_recovery,
                       self.data.get("ac_target", 22.0) + config.rack_recovery_offset)
        recovered = rack is not None and rack <= recovery
        requested = (self.status.alarm.thermal_critical
                     or (rack is not None and config.warning_on is not None and rack > config.warning_on))
        if recovered:
            self.emergency_ac_target = None
        elif (self.emergency_ac_target is None and requested
              and self.status.decision.desired is Mode.AIR_CONDITIONING and climate_state is not None):
            measured = state.ac_temperature.temperature(state.now, self.data["max_age"]) if state.ac_temperature else None
            attrs = climate_state.attributes
            minimum, maximum, step = (attrs.get(k) for k in ("min_temp", "max_temp", "target_temp_step"))
            # No invented device limits or resolution. Missing metadata leaves
            # normal cooling intact and can be supplied by a later publication.
            if self.hass.config.units.temperature_unit == "°F":
                minimum = (minimum - 32) * 5 / 9 if type(minimum) in (int, float) else None
                maximum = (maximum - 32) * 5 / 9 if type(maximum) in (int, float) else None
                step = step * 5 / 9 if type(step) in (int, float) else None
            from .core.boost import emergency_target
            self.emergency_ac_target = emergency_target(self.data.get("ac_target", 22.0), measured, minimum, maximum, step)
        if previous != self.emergency_ac_target:
            self.telemetry.record("emergency_ac_target", active=self.emergency_ac_target is not None,
                                  target=self.active_ac_target, normal_target=self.data.get("ac_target", 22.0),
                                  rack_temperature=rack, warning_threshold=config.warning_on,
                                  recovery_target=recovery)

    async def _save_reserve(self):
        async with self.reserve_lock:
            await self.reserve_store.async_save({"hold": self.observer.reserve_hold})

    async def async_prepare(self):
        saved = await self.reserve_store.async_load()
        self.observer.reserve_hold = isinstance(saved, dict) and saved.get("hold") is True
        self._saved_reserve = self.observer.reserve_hold
        await self.voice.async_restore()
        if not self.haier:
            return
        from .config_flow import validate_haier_binding
        validate_haier_binding(self.hass, self.data)
        from homeassistant.components import mqtt
        cancel = await mqtt.async_subscribe(self.hass, self.data["haier_result_topic"], self._haier_result, qos=0)
        self.unsubscribers.append(cancel)
        self.haier_result_ready = True

    @callback
    def _haier_result(self, message):
        if self.stopped or not self.haier:
            return
        import json
        try:
            payload = json.loads(message.payload)
        except (ValueError, TypeError):
            return
        self.haier.result(payload, monotonic(), retained=message.retain)
        self.refresh()

    async def async_set_control(self, enabled):
        """Persist operator intent, independently of shutdown cleanup."""
        async with self.control_lock:
            if self.stopped:
                raise HomeAssistantError("SRCC is unloading")
            if enabled:
                if not self.data.get("control_profile_enabled"):
                    raise HomeAssistantError("Configure the execution profile first")
                validate(self.data)
            else:
                self.executor.disable()
            await self.control_store.async_save({"enabled": enabled})
            if enabled and not self.stopped:
                # An explicit OFF -> ON permits a supervised ventilation retry.
                # Automatic restoration after restart does not clear the hold.
                if not self.executor.enabled:
                    self.observer.reserve_hold = False
                self.executor.enable()

    async def async_restore_control(self):
        """Restore intent only; plans and commands are always rebuilt."""
        saved = await self.control_store.async_load()
        if not isinstance(saved, dict) or saved.get("enabled") is not True:
            return
        if not self.data.get("control_profile_enabled"):
            await self.control_store.async_save({"enabled": False})
            self.telemetry.record("automation_restore_skipped", reason="PROFILE_DISABLED")
            return
        if not self.stopped:
            self.telemetry.record("automation_restored")
            self.executor.enable()

    @callback
    def start(self):
        entity_ids = [self.data[key] for key in ENTITY_ROLES if key in self.data]
        self.unsubscribers.append(async_track_state_change_event(self.hass, entity_ids, self._changed))
        # Watchdog has no device polling. It notices age expiry without a state
        # change and serves minute-based sampling even for unchanged values.
        self.unsubscribers.append(async_track_time_interval(self.hass, self._tick, timedelta(seconds=1)))
        self.refresh()
        self.voice.start()

    @callback
    def stop(self):
        self.stopped = True
        self.voice.stop()
        self.executor.disable()
        self.haier_result_ready = False
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
        if self.haier and event.data.get("entity_id") == self.data["ac_telemetry"]:
            state = event.data.get("new_state")
            if state is None or state.state in ("unknown", "unavailable") or state.attributes.get("restored"):
                self.haier.invalidate("TELEMETRY_ENTITY_UNAVAILABLE")
            else:
                attributes = dict(state.attributes)
                # MQTT strips reserved `available` from HA JSON attributes.
                # Schema 1 fault_valid=True implies a fresh UART status AND
                # valid protocol connection. Never infer availability merely
                # from the entity's numeric state or a false fault value.
                if "available" not in attributes:
                    attributes["available"] = attributes.get("fault_valid") is True
                self.haier.receive(attributes, monotonic())
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

        samples = {}

        def read(role, kind, attribute=None):
            entity_id = self.data.get(role)
            sample = read_sample(self.hass.states.get(entity_id) if entity_id else None,
                               source=entity_id or role, now=now, wall_now=wall_now,
                               kind=kind, attribute=attribute,
                               clock_anchor=(self._clock_anchor, self._wall_anchor))
            if role == "climate":
                sample = replace(sample, freshness_limit=self.data.get("ac_max_age", 180.0))
            if entity_id:
                samples[role] = sample
            return sample

        backup_roles = tuple(key for key in ("bolid_temperature_1", "bolid_temperature_2")
                             if self.data.get(key))
        if backup_roles:
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
            ac_temperature = read_sample(proxy, source=climate_state.entity_id + ":current", now=now,
                                         wall_now=wall_now, kind="temperature", attribute="current_temperature",
                                         clock_anchor=(self._clock_anchor, self._wall_anchor))
        else:
            ac_temperature = Sample(None, "ac_temperature", now, Quality.UNAVAILABLE)
            target = Sample(None, "ac_target", now, Quality.UNAVAILABLE)
        target = replace(target, freshness_limit=self.data.get("ac_max_age", 180.0))
        ac_temperature = replace(ac_temperature, freshness_limit=self.data.get("ac_max_age", 180.0))
        climate_unavailable = mode.quality is Quality.UNAVAILABLE
        if self.haier:
            mode, target, fault = self.haier.samples(now)
            ac_temperature = self.haier.temperature_sample(now)
            samples["climate"] = mode
            samples["ac_telemetry"] = mode
            samples["ac_fault"] = fault
        state = Snapshot(
            now=now, tin=read("trm_temperature", "temperature"),
            tout=read("outdoor_temperature", "temperature"),
            power=power_state(read("ups_online", "bool"), read("ups_on_battery", "bool"), now, age),
            ac_available=mode.fresh(now, age),
            vent_available=enabled.fresh(now, age) and output.fresh(now, age),
            vent_enabled=enabled, vent_output=output, ac_mode=mode, ac_target=target,
            ac_temperature=ac_temperature,
            ac_fault=fault.value if fault.fresh(now, age) else None,
            ac_fault_monitored=bool(self.haier or self.data.get("ac_fault")),
            ac_command_confirmed=self.haier.confirmed(now) if self.haier else None,
            vent_ineffective=self.vent_failed,
            ac_unpowered=(self.data.get("shared_mains_confirmed") is True
                          and climate_unavailable
                          and not (self.haier and mode.fresh(now, age) and mode.value != "off")
                          and read("ups_online", "bool").fresh(now, age)
                          and read("ups_online", "bool").value is False
                          and read("ups_on_battery", "bool").fresh(now, age)
                          and read("ups_on_battery", "bool").value is True),
        )
        self.reported_mode = reported_mode(state, age)
        current = Mode(self.reported_mode) if self.reported_mode not in ("unknown", "conflict") else None
        state = replace(state, current=current)
        self.snapshot = state
        from .core.inputs import select_temperature, Fallback
        selection = select_temperature(state.tin, self.backups, now=now, max_age=age,
                                       fallback=Fallback.MEAN_ALLOW_ONE, max_skew=self.data["backup_max_skew"])
        value = selection.sample.value if selection.sample else None
        config = self.observer.config
        if value is None or config.emergency_on is None:
            self.demand = None
        elif value >= config.emergency_on or value >= config.critical_on:
            self.demand = True
        elif value <= config.emergency_off:
            self.demand = False
        elif self.demand is None:
            self.demand = state.proved(state.vent_output, True, age)
        self.status = self.observer.evaluate(state, backups=self.backups, cooling_required=self.demand,
                                             other_critical=self.reported_mode == "conflict" and not self.executor.enabled)
        self._update_emergency_target(state, climate_state)
        if self.observer.reserve_hold != self._saved_reserve:
            self._saved_reserve = self.observer.reserve_hold
            self.reserve_task = self.hass.async_create_task(self._save_reserve())
            self.telemetry.record("vent_retry_hold", active=self.observer.reserve_hold)
        if self.status.trend.ineffective is True:
            self.vent_failed = True
        self.input_status = {}
        for role, sample in samples.items():
            quality = sample.quality.value
            if quality == "valid" and not sample.fresh(now, age):
                quality = "stale"
            source_id = self.data.get("ac_telemetry") if self.haier and role in ("climate", "ac_fault", "ac_telemetry") else self.data.get(role)
            source_state = self.hass.states.get(source_id) if source_id else None
            reported = getattr(source_state, "last_reported", None)
            self.input_status[role] = {
                "quality": quality,
                "publication_age_s": round((wall_now - reported).total_seconds(), 1) if reported else None,
                "max_age_s": sample.freshness_limit if sample.freshness_limit is not None else age,
            }
        if self.haier:
            details = self.haier.diagnostics(now)
            for role in ("climate", "ac_telemetry", "ac_fault"):
                self.input_status[role].update(
                    source_age_lower_bound_s=details["source_age_lower_bound_s"],
                    heartbeat_age_s=details["heartbeat_age_s"],
                    heartbeat_limit_s=15.0, max_age_s=30.0,
                    freshness_basis="haier_uart_sample",
                    telemetry_reason=details["reason"])
        self.telemetry.observe(self)
        self.notify()
        self.executor.schedule()

    @callback
    def notify(self):
        for listener in tuple(self.listeners):
            listener()
