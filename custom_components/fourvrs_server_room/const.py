"""HA adapter constants; operational defaults live in the core Config."""
DOMAIN = "fourvrs_server_room"
NAME = "Server Room Climate Controller"
VERSION = "0.1.0"
ENTITY_ROLES = {
    "trm_temperature": ["sensor"],
    "outdoor_temperature": ["sensor"],
    "bolid_temperature_1": ["sensor"],
    "bolid_temperature_2": ["sensor"],
    "vent_enable": ["number", "sensor", "switch", "binary_sensor"],
    "vent_output": ["binary_sensor", "switch"],
    "climate": ["climate"],
    "ac_fault": ["binary_sensor"],
    "ups_online": ["binary_sensor"],
    "ups_on_battery": ["binary_sensor"],
}
REQUIRED_ROLES = ("trm_temperature", "outdoor_temperature")
TIMING_FIELDS = ("max_age", "backup_max_skew", "sample_tolerance", "recovery_max_gap")
CONFIG_FIELDS = (
    "warning_on", "critical_on", "critical_recovery", "recovery_hold",
    "ac_hard_min", "vent_to_ac", "ac_to_vent",
)
