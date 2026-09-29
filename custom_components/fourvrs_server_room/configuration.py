"""Validation shared by setup and Config Flow; no equipment side effects."""
from math import isfinite

from .const import CONFIG_FIELDS, ENTITY_ROLES, REQUIRED_ROLES, TIMING_FIELDS, OPTIONAL_CORE_FIELDS, EXECUTION_DEFAULTS, EXECUTION_FIELDS
from .core.models import Config


class ConfigurationError(ValueError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


def core_config(data: dict) -> Config:
    return Config(**{key: data[key] for key in (*CONFIG_FIELDS, *OPTIONAL_CORE_FIELDS) if key in data})


def validate(data: dict) -> dict:
    result = {key: value for key, value in data.items() if value != ""}
    for key in ("voice_info", "voice_warning", "voice_error", "voice_repeat"):
        result.setdefault(key, False)
        if type(result[key]) is not bool:
            raise ConfigurationError("invalid_voice_settings")
    if "voice_area" in result and (not isinstance(result["voice_area"], str) or not result["voice_area"]):
        raise ConfigurationError("invalid_voice_settings")
    for role in REQUIRED_ROLES:
        if role not in result:
            raise ConfigurationError("missing_entity")
    result.setdefault("ac_max_age", 180.0)
    ac_age = result["ac_max_age"]
    if isinstance(ac_age, bool) or not isinstance(ac_age, (int, float)) or not isfinite(ac_age) or ac_age <= 0:
        raise ConfigurationError("invalid_timing")
    if result.get("ac_telemetry"):
        if not result.get("climate"):
            raise ConfigurationError("missing_control_entities")
        topic = result.get("haier_result_topic", "")
        if (not isinstance(topic, str) or not topic or len(topic) > 256
                or any(c in topic for c in ("+", "#", "\x00")) or not topic.endswith("/result")):
            raise ConfigurationError("invalid_haier_result_topic")
    selected = []
    for role, domains in ENTITY_ROLES.items():
        if role not in result:
            continue
        entity_id = result[role]
        if not isinstance(entity_id, str) or entity_id.count(".") != 1:
            raise ConfigurationError("invalid_entity")
        domain, name = entity_id.split(".")
        if domain not in domains or not name or not name.replace("_", "").isalnum():
            raise ConfigurationError("invalid_entity")
        selected.append(entity_id)
    if len(selected) != len(set(selected)):
        raise ConfigurationError("duplicate_entity")
    for key in TIMING_FIELDS:
        value = result.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ConfigurationError("invalid_timing")
        if value < 0 or (key in ("max_age", "recovery_max_gap") and value == 0):
            raise ConfigurationError("invalid_timing")
    if result["sample_tolerance"] >= 60:
        raise ConfigurationError("invalid_timing")
    # Reading HA's publication timestamp is an explicit observation contract,
    # not a claim that hardware is physically reachable on each publication.
    if result.get("reported_freshness_confirmed") is not True:
        raise ConfigurationError("freshness_confirmation_required")
    try:
        core_config(result)
    except (ValueError, TypeError) as error:
        raise ConfigurationError("invalid_thresholds") from error
    for key in EXECUTION_FIELDS:
        if key not in result:
            continue
        value = result[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not isfinite(value):
            raise ConfigurationError("invalid_execution")
        if key != "ac_target" and (value < 0 or (key in ("command_timeout", "ac_readback_timeout", "haier_readback_timeout") and value == 0)):
            raise ConfigurationError("invalid_execution")
    if result.get("control_profile_enabled", False):
        if not all(result.get(key) for key in ("climate", "vent_enable", "vent_output",
                                                "ups_online", "ups_on_battery")):
            raise ConfigurationError("missing_control_entities")
        if result["vent_enable"].split(".")[0] not in ("number", "switch") or not result["vent_output"].startswith("switch."):
            raise ConfigurationError("control_entities_not_writable")
        if "ac_target" not in result or not all(key in result for key in OPTIONAL_CORE_FIELDS[:3]):
            raise ConfigurationError("missing_control_settings")
        if result.get("equipment_readback_confirmed") is not True:
            raise ConfigurationError("control_readback_required")
        for key, value in EXECUTION_DEFAULTS.items():
            result.setdefault(key, value)
    return result
