"""Observation sensors; reported equipment modes are not physical guarantees."""
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature

from .entity import SrccEntity

KEYS = ("temperature", "outdoor_temperature", "temperature_source", "alarm_severity",
        "reported_mode", "desired_mode", "decision_reason", "ventilation_trend",
        "observation_samples", "power_state")


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities(SrccSensor(entry.runtime_data, key) for key in KEYS)


class SrccSensor(SrccEntity, SensorEntity):
    def __init__(self, runtime, key):
        super().__init__(runtime, key)
        if key in ("temperature", "outdoor_temperature"):
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key == "ventilation_trend":
            self._attr_native_unit_of_measurement = "°C/min"

    @property
    def native_value(self):
        status, snapshot = self.runtime.status, self.runtime.snapshot
        if status is None:
            return None
        sample = status.temperature.sample
        values = {
            "temperature": sample.value if sample else None,
            "outdoor_temperature": snapshot.tout.temperature(snapshot.now, self.runtime.data["max_age"]),
            "temperature_source": ({"TRM_PRIMARY": "trm", "BOLID_MEAN_FALLBACK": "bolid_mean",
                                    "BOLID_SINGLE_FALLBACK": "bolid_single"}.get(status.temperature.reason)) ,
            "alarm_severity": status.alarm.severity.value,
            "reported_mode": self.runtime.reported_mode,
            "desired_mode": status.decision.desired.value if status.decision.desired else None,
            "decision_reason": status.decision.reason,
            "ventilation_trend": status.trend.rate_per_minute,
            "observation_samples": status.trend.samples,
            "power_state": snapshot.power.value,
        }
        return values[self.key]

    @property
    def extra_state_attributes(self):
        status = self.runtime.status
        if status is None:
            return None
        if self.key == "decision_reason":
            return {"observation_only": not self.runtime.executor.enabled, "execution_phase": self.runtime.executor.step.phase,
                    "execution_reason": self.runtime.executor.step.reason, "execution_error": self.runtime.executor.error, "blockers": list(status.decision.blockers),
                    "urgent": status.decision.urgent,
                    "last_command": self.runtime.executor.last_command,
                    "recovery_basis": "rack_temperature",
                    "ac_measured_temperature": self.runtime.snapshot.ac_temperature.temperature(self.runtime.snapshot.now, self.runtime.data["max_age"]) if self.runtime.snapshot.ac_temperature else None,
                    "rack_recovery_target": min(self.runtime.observer.config.critical_recovery, self.runtime.observer.ac_target + self.runtime.observer.config.rack_recovery_offset),
                    "vent_retry_locked": self.runtime.observer.reserve_hold,
                    "active_ac_target": self.runtime.active_ac_target,
                    "emergency_ac_target": self.runtime.emergency_ac_target,
                    "adaptation_episode_closed": self.runtime.adaptation.closed,
                    "adaptation_pending": self.runtime.adaptation.pending,
                    "boost_recovery_target": self.runtime.boost_recovery_target,
                    "rack_recovery_limit": self.runtime.observer.config.critical_recovery,
                    "input_issues": [role for role, item in self.runtime.input_status.items() if item["quality"] != "valid"]}
        if self.key == "temperature_source":
            return {"selection_reason": status.temperature.reason,
                    "source": status.temperature.sample.source if status.temperature.sample else None,
                    "unavailable_backups": list(status.temperature.warning_sources)}
        return None
