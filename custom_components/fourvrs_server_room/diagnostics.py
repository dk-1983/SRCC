"""Minimal diagnostics without source entity IDs or raw external attributes."""
from .const import CONFIG_FIELDS, ENTITY_ROLES, TIMING_FIELDS, VERSION


async def async_get_config_entry_diagnostics(hass, entry):
    runtime = entry.runtime_data
    status = runtime.status
    return {
        "version": VERSION,
        "inputs": runtime.input_status,
        "recent_events": list(runtime.telemetry.events),
        "wait_remaining": dict(runtime.executor.wait_details),
        "last_command": runtime.executor.last_command,
        "observation_only": not runtime.executor.enabled,
        "execution_phase": runtime.executor.step.phase,
        "execution_error": bool(runtime.executor.error),
        "bound_roles": [role for role in ENTITY_ROLES if role in runtime.data],
        "settings": {key: runtime.data.get(key) for key in (*CONFIG_FIELDS, *TIMING_FIELDS)},
        "reported_mode": runtime.reported_mode,
        "decision": status.decision.reason if status else None,
        "severity": status.alarm.severity.value if status else None,
        "temperature_selection": status.temperature.reason if status else None,
        "unavailable_backup_count": len(status.temperature.warning_sources) if status else None,
        "trend_samples": status.trend.samples if status else 0,
        "degraded": status.degraded if status else True,
    }
