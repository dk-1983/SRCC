"""Minimal diagnostics without source entity IDs or raw external attributes."""
from .const import CONFIG_FIELDS, ENTITY_ROLES, TIMING_FIELDS, VERSION, EXECUTION_DEFAULTS


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    status = runtime.status
    return {
        "version": VERSION,
        "thermal_recovery": {
            "basis": "rack_temperature",
            "rack_target": min(runtime.observer.config.critical_recovery, runtime.observer.ac_target + runtime.observer.config.rack_recovery_offset),
            "vent_retry_locked": runtime.observer.reserve_hold,
            "rack_limit": runtime.observer.config.critical_recovery,
            "ac_temperature": (runtime.snapshot.ac_temperature.temperature(
                runtime.snapshot.now, runtime.data["max_age"])
                if runtime.snapshot.ac_temperature else None),
        },
        "voice": runtime.voice.diagnostics(),
        "ac_telemetry": runtime.haier.diagnostics(runtime.snapshot.now) if runtime.haier else None,
        "inputs": runtime.input_status,
        "recent_events": list(runtime.telemetry.events),
        "wait_remaining": dict(runtime.executor.wait_details),
        "last_command": runtime.executor.last_command,
        "observation_only": not runtime.executor.enabled,
        "execution_phase": runtime.executor.step.phase,
        "execution_error": bool(runtime.executor.error),
        "bound_roles": [role for role in ENTITY_ROLES if role in runtime.data],
        "settings": {key: runtime.data.get(key) for key in (*CONFIG_FIELDS, *TIMING_FIELDS, "ac_max_age", *EXECUTION_DEFAULTS)},
        "reported_mode": runtime.reported_mode,
        "decision": status.decision.reason if status else None,
        "severity": status.alarm.severity.value if status else None,
        "temperature_selection": status.temperature.reason if status else None,
        "unavailable_backup_count": len(status.temperature.warning_sources) if status else None,
        "trend_samples": status.trend.samples if status else 0,
        "degraded": status.degraded if status else True,
    }
