"""Validation shared by setup and Config Flow; no equipment side effects."""
from math import isfinite

from .const import CONFIG_FIELDS, ENTITY_ROLES, REQUIRED_ROLES, TIMING_FIELDS
from .core.models import Config


class ConfigurationError(ValueError):
    def __init__(self, key: str):
        self.key = key
        super().__init__(key)


def core_config(data: dict) -> Config:
    return Config(**{key: data[key] for key in CONFIG_FIELDS if key in data})


def validate(data: dict) -> dict:
    result = {key: value for key, value in data.items() if value != ""}
    for role in REQUIRED_ROLES:
        if role not in result:
            raise ConfigurationError("missing_entity")
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
    if bool(result.get("bolid_temperature_1")) != bool(result.get("bolid_temperature_2")):
        raise ConfigurationError("backup_pair_required")
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
    return result
