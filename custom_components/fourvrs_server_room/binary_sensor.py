"""Diagnostic flags; none can enable equipment."""
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass

from .entity import SrccEntity

KEYS = ("controller_degraded", "backup_sensor_problem", "thermal_critical", "observation_only")


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(SrccBinarySensor(entry.runtime_data, key) for key in KEYS)


class SrccBinarySensor(SrccEntity, BinarySensorEntity):
    def __init__(self, runtime, key):
        super().__init__(runtime, key)
        if key != "observation_only":
            self._attr_device_class = BinarySensorDeviceClass.PROBLEM

    @property
    def is_on(self):
        status = self.runtime.status
        if status is None:
            return None
        return {
            "controller_degraded": bool(self.runtime.executor.error) or (self.runtime.executor.enabled
                and self.runtime.executor.step.phase == "BLOCKED") or status.degraded or status.decision.desired is None or not self.runtime.snapshot.tout.fresh(
                self.runtime.snapshot.now, self.runtime.data["max_age"]),
            "backup_sensor_problem": bool(status.temperature.warning_sources),
            "thermal_critical": status.alarm.thermal_critical,
            "observation_only": not self.runtime.executor.enabled,
        }[self.key]
