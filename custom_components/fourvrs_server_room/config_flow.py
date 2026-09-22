"""Observation inputs and optional command-profile configuration."""
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.helpers import selector

from .configuration import ConfigurationError, validate
from .const import CONFIG_FIELDS, DOMAIN, ENTITY_ROLES, NAME, REQUIRED_ROLES, TIMING_FIELDS, OPTIONAL_CORE_FIELDS, EXECUTION_DEFAULTS, CONTROL_TEMPERATURE_DEFAULTS
from .core.models import Config


def schema(defaults=None):
    defaults = defaults or {}
    fields = {}
    for role, domains in ENTITY_ROLES.items():
        marker = vol.Required if role in REQUIRED_ROLES else vol.Optional
        fields[marker(role, description={"suggested_value": defaults.get(role)})] = selector.EntitySelector(
            selector.EntitySelectorConfig(domain=domains))
    for key in TIMING_FIELDS:
        fields[vol.Required(key, description={"suggested_value": defaults.get(key)})] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, step="any", mode=selector.NumberSelectorMode.BOX,
                                          unit_of_measurement="s"))
    fields[vol.Optional("ac_max_age", default=defaults.get("ac_max_age", 180.0))] = selector.NumberSelector(
        selector.NumberSelectorConfig(min=1, step="any", mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"))
    for key in CONFIG_FIELDS:
        fields[vol.Required(key, default=defaults.get(key, getattr(Config(), key)))] = selector.NumberSelector(
            selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX,
                                          unit_of_measurement="s" if key == "recovery_hold" else "°C"))
    fields[vol.Required("reported_freshness_confirmed", default=defaults.get(
        "reported_freshness_confirmed", False))] = selector.BooleanSelector()
    for key in ("control_profile_enabled", "equipment_readback_confirmed", "shared_mains_confirmed"):
        fields[vol.Optional(key, default=defaults.get(key, False))] = selector.BooleanSelector()
    for key in ("ac_target", *OPTIONAL_CORE_FIELDS):
        fields[vol.Optional(key, description={"suggested_value": defaults.get(key, CONTROL_TEMPERATURE_DEFAULTS.get(key))})] = selector.NumberSelector(
            selector.NumberSelectorConfig(step="any", mode=selector.NumberSelectorMode.BOX, unit_of_measurement="°C"))
    for key, value in EXECUTION_DEFAULTS.items():
        fields[vol.Optional(key, default=defaults.get(key, value))] = selector.NumberSelector(
            selector.NumberSelectorConfig(min=0, step="any", mode=selector.NumberSelectorMode.BOX, unit_of_measurement="s"))
    return vol.Schema(fields)


def validate_with_hass(hass, data):
    data = validate(data)
    for role in ENTITY_ROLES:
        entity_id = data.get(role)
        if entity_id is None:
            continue
        state = hass.states.get(entity_id)
        if state is None:
            raise ConfigurationError("entity_not_found")
        if "temperature" in role and state.state not in ("unknown", "unavailable"):
            if state.attributes.get("unit_of_measurement") not in ("°C", "°F", "K"):
                raise ConfigurationError("invalid_temperature_unit")
        # Never allow an SRCC output to become its own input.
        from homeassistant.helpers import entity_registry as er
        registered = er.async_get(hass).async_get(entity_id)
        if registered is not None and registered.platform == DOMAIN:
            raise ConfigurationError("self_reference")
    return data


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None):
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        errors = {}
        if user_input is not None:
            try:
                data = validate_with_hass(self.hass, user_input)
            except ConfigurationError as error:
                errors["base"] = error.key
            else:
                return self.async_create_entry(title=NAME, data=data)
        return self.async_show_form(step_id="user", data_schema=schema(user_input), errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return OptionsFlow()


class OptionsFlow(config_entries.OptionsFlow):
    async def async_step_init(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                data = validate_with_hass(self.hass, user_input)
            except ConfigurationError as error:
                errors["base"] = error.key
            else:
                # Store a full replacement, including cleared optional bindings.
                return self.async_create_entry(title="", data=data)
        defaults = dict(self.config_entry.options or self.config_entry.data)
        if user_input is not None:
            defaults = user_input
        return self.async_show_form(step_id="init", data_schema=schema(defaults), errors=errors)
