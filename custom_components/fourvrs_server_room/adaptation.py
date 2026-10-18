"""One persistent downward step per rack warning episode; no upward learning."""
import asyncio

from homeassistant.helpers.storage import Store

from .core.boost import emergency_target
from .core.models import Mode


class DownwardAdaptation:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = Store(runtime.hass, 1, f"fourvrs_server_room.{runtime.entry.entry_id}.adaptation")
        self.lock = asyncio.Lock()
        self.closed = False
        self.pending = None
        self.task = None
        self.limit_reported = False

    async def load(self):
        saved = await self.store.async_load()
        self.closed = isinstance(saved, dict) and saved.get("closed") is True

    async def save(self):
        async with self.lock:
            await self.store.async_save({"closed": self.closed})

    def update(self, state, climate):
        rt = self.runtime
        sample = rt.status.temperature.sample
        rack = sample.temperature(state.now, rt.data["max_age"]) if sample else None
        warning = rt.observer.config.warning_on
        if warning is None or rack is None:
            return
        if rack <= warning - 1:
            self.limit_reported = False
        if self.closed and self.pending is None and rack <= warning - 1:
            self.closed = False
            self.limit_reported = False
            self.task = rt.hass.async_create_task(self.save())
        if (self.closed or self.pending is not None or not rt.executor.enabled
                or rack < warning or rt.status.alarm.thermal_critical
                or state.current is not Mode.AIR_CONDITIONING
                or rt.status.decision.desired is not Mode.AIR_CONDITIONING or climate is None):
            return
        normal = rt.data["ac_target"]
        minimum, maximum, step = rt.ac_limits(climate)
        target = emergency_target(normal, normal, minimum, maximum, step)
        if target is None:
            return
        if target >= normal:
            if not self.limit_reported:
                rt.telemetry.record("adaptation_limit", warning=True, target=normal, minimum=minimum)
                self.limit_reported = True
            return
        self.pending = {"previous": normal, "target": target}
        rt.telemetry.record("adaptation_requested", previous=normal, target=target,
                            rack_temperature=rack, warning_threshold=warning)

    async def before_send(self, target):
        if self.pending and target <= self.pending["target"]:
            # Persist the episode before sending: reload cannot cause another step.
            self.closed = True
            await self.save()

    def confirmed(self, target):
        if not self.pending or target != self.pending["target"]:
            return
        rt = self.runtime
        previous = self.pending["previous"]
        # Preserve all other fields; the ordinary options form reads this value.
        options = dict(rt.entry.options or rt.entry.data)
        options["ac_target"] = target
        from .configuration import validate
        new_data = validate(options)
        old_data = rt.data
        rt.data = new_data
        try:
            rt.hass.config_entries.async_update_entry(rt.entry, options=options)
        except Exception:
            rt.data = old_data
            raise
        rt.observer.ac_target = target
        self.pending = None
        if rt.executor.last_command:
            rt.executor.last_command = dict(rt.executor.last_command, adapted_from=previous)
        rt.telemetry.record("adaptation_saved", previous=previous, target=target,
                            reason="RACK_WARNING")
