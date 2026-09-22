"""Server Room Climate Controller: read-only Home Assistant observation."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = ("sensor", "binary_sensor")


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from .configuration import validate
    from .runtime import Runtime

    data = validate(dict(entry.options or entry.data))
    runtime = Runtime(hass, entry, data)
    entry.runtime_data = runtime
    entry.async_on_unload(runtime.stop)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    runtime.start()
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        runtime.stop()
        raise
    return True


async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        entry.runtime_data.stop()
    return unloaded
