"""Common read-only entity subscription and identity."""
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, NAME, VERSION


class SrccEntity(Entity):
    _attr_should_poll = False
    _attr_has_entity_name = True

    def __init__(self, runtime, key):
        self.runtime, self.key = runtime, key
        self._attr_unique_id = f"{runtime.entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = {
            "identifiers": {(DOMAIN, runtime.entry.entry_id)},
            "name": NAME,
            "manufacturer": "4VRS",
            "model": "Climate controller",
            "sw_version": VERSION,
        }

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.subscribe(self.async_write_ha_state))

    @property
    def available(self):
        return self.runtime.status is not None and not self.runtime.stopped
