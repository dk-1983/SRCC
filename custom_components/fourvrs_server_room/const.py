"""HA adapter constants; operational defaults live in the core Config."""
DOMAIN = "fourvrs_server_room"
NAME = "Server Room Climate Controller"
VERSION = "0.2.3"
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

OPTIONAL_CORE_FIELDS = ("emergency_on", "emergency_off", "emergency_delta", "minimum_vent_rise", "emergency_outdoor_max")
EXECUTION_DEFAULTS = {"command_timeout": 30.0, "condition_hold": 30.0,
                      "minimum_dwell": 300.0, "minimum_ac_off": 180.0}
EXECUTION_FIELDS = ("ac_target", *EXECUTION_DEFAULTS)

CONTROL_TEMPERATURE_DEFAULTS = {"ac_target": 22.0, "emergency_on": 24.0, "emergency_off": 20.0,
                                "emergency_delta": 0.0, "emergency_outdoor_max": 10.0}
