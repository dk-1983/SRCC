"""Server Room Climate Controller: Home Assistant observation and opt-in execution."""
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

PLATFORMS = ("sensor", "binary_sensor", "switch")


async def async_setup_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    from .configuration import validate
    from .runtime import Runtime

    data = validate(dict(entry.options or entry.data))
    runtime = Runtime(hass, entry, data)
    entry.runtime_data = runtime
    entry.async_on_unload(runtime.stop)
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    try:
        await runtime.async_prepare()
        runtime.start()
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        await runtime.async_restore_control()
    except Exception:
        runtime.stop()
        raise
    return True


async def _options_updated(hass, entry):
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: "HomeAssistant", entry: "ConfigEntry") -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        runtime = entry.runtime_data
        runtime.stop()
        if runtime.executor.task is not None:
            await runtime.executor.task
        if runtime.voice.closing_task is not None:
            await runtime.voice.closing_task
    return unloaded
