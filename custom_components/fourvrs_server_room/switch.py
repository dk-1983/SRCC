"""Persistent control preference; equipment is reconciled on startup."""
from homeassistant.components.switch import SwitchEntity
from .entity import SrccEntity

async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([AutomaticSwitch(entry.runtime_data, "automatic_control"),
                        MaintenanceSwitch(entry.runtime_data, "voice_maintenance")])

class AutomaticSwitch(SrccEntity, SwitchEntity):
    @property
    def is_on(self):
        return self.runtime.executor.enabled

    async def async_turn_on(self, **kwargs):
        await self.runtime.async_set_control(True)

    async def async_turn_off(self, **kwargs):
        await self.runtime.async_set_control(False)


class MaintenanceSwitch(SrccEntity, SwitchEntity):
    """Mute this process's voice notifications without changing equipment."""
    @property
    def is_on(self):
        return self.runtime.voice.maintenance

    async def async_turn_on(self, **kwargs):
        await self.runtime.voice.async_set_maintenance(True)

    async def async_turn_off(self, **kwargs):
        await self.runtime.voice.async_set_maintenance(False)
