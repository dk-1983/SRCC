"""Optional public Home Control v1 client. Never imports or controls the center."""
import asyncio
from datetime import timedelta
import logging
from uuid import uuid4

from homeassistant.core import callback
from homeassistant.helpers import device_registry
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import DOMAIN, NAME
from .voice_events import VoiceProjection

_LOGGER = logging.getLogger(__name__)
PREFERENCES = ("voice_info", "voice_warning", "voice_error", "voice_repeat")
REGISTER = "register_voice_source"
PUBLISH = "publish_voice_event"
UNREGISTER = "unregister_voice_source"
READY = "home_control_voice_ready"
DISCOVER = "home_control_voice_discover"
LEASE = 180
RENEW = 60
CALL_TIMEOUT = 3


class ProcessVoice:
    def __init__(self, runtime):
        self.runtime, self.hass = runtime, runtime.hass
        self.source = dict(domain=DOMAIN, config_entry_id=runtime.entry.entry_id, process_id="climate")
        self.session = uuid4().hex
        self.revision = 0
        self.center_session = None
        self.registered = False
        self.maintenance = True  # Until the persisted preference is loaded.
        self.store = Store(self.hass, 1, f"{DOMAIN}.{runtime.entry.entry_id}.voice")
        self.lock = asyncio.Lock()
        self.projection = VoiceProjection()
        self.current = {}
        self.sent = {}
        self.infos = {}  # Only current transition notices, never an offline backlog.
        self.metadata = None
        self.need_register = True
        self.task = None
        self.closing_task = None
        self.unsubscribers = []
        self.started = False
        self.stopped = False
        self.retry_at = 0.0
        self.generation = 0
        self.connection = "not_started"

    async def async_restore(self):
        try:
            saved = await self.store.async_load()
            self.maintenance = isinstance(saved, dict) and saved.get("maintenance") is True
        except Exception:
            self.maintenance = True
            _LOGGER.exception("Voice maintenance preference unavailable; notifications remain muted")

    async def async_set_maintenance(self, enabled):
        async with self.lock:
            if self.stopped:
                return
            # Muting takes effect locally even if persistence fails.
            if enabled:
                self.maintenance = True
                self.infos.clear()
                self.observe()
            await self.store.async_save({"maintenance": enabled})
            self.maintenance = enabled
            self.observe()
            self.runtime.notify()

    def _metadata(self):
        area = self.runtime.data.get("voice_area")
        if not area:
            try:
                device = device_registry.async_get(self.hass).async_get_device_by_identifier(
                    (DOMAIN, self.runtime.entry.entry_id), self.runtime.entry.entry_id)
            except (KeyError, RuntimeError):  # Registry may not exist before HA platform setup.
                device = None
            area = device.area_id if device else None
        return dict(name=(getattr(self.runtime.entry, "title", NAME) or NAME)[:200], area_id=area,
                    preferences={key: self.runtime.data.get(key, False) for key in PREFERENCES},
                    maintenance=self.maintenance)

    @callback
    def start(self):
        if self.started:
            return
        self.started = True
        self.unsubscribers = [self.hass.bus.async_listen(READY, self._ready),
                              async_track_time_interval(self.hass, self._renew, timedelta(seconds=RENEW)),
                              self.runtime.subscribe(self.observe)]
        self.hass.bus.async_fire(DISCOVER, {"protocol_version": 1})
        self.observe()

    @callback
    def _ready(self, event):
        data = event.data
        session = data.get("center_session")
        if type(data.get("protocol_version")) is not int or data["protocol_version"] != 1 or not isinstance(session, str) or not session:
            return
        if session != self.center_session:
            self.generation += 1
            self.center_session = session
            self.registered = False
            self.infos.clear()
            self.need_register = True
            self.retry_at = 0
            self._schedule()

    @callback
    def _renew(self, _now):
        self.need_register = True
        self.observe()
        self._schedule()

    @callback
    def observe(self):
        if self.stopped or not self.started:
            return
        try:
            current, notices = self.projection.update(self.runtime)
            changed = current != self.current or self.metadata != self._metadata()
            self.current = current
            for key in current:
                self.infos.pop(key, None)  # A recurring issue invalidates queued recovery INFO.
            if self.maintenance or not self.registered:
                self.infos.clear()
            else:
                for notice in notices:
                    if self.runtime.data.get("voice_info", False):
                        self.infos[notice["key"]] = notice
            if changed or notices or self.need_register:
                self._schedule()
        except Exception:
            # Failures in this observer never escape into equipment callbacks.
            _LOGGER.exception("Could not prepare SRCC voice observations")

    def _schedule(self):
        if self.stopped or self.hass.loop.time() < self.retry_at:
            return
        if not all(self.hass.services.has_service("home_control", name) for name in (REGISTER, PUBLISH, UNREGISTER)):
            self.registered = False
            self.infos.clear()
            self.connection = "center_unavailable"
            return
        if self.task is None or self.task.done():
            self.task = self.hass.async_create_task(self._pump(), eager_start=False)

    def _envelope(self):
        self.revision += 1
        return dict(protocol_version=1, source=self.source.copy(), producer_session=self.session,
                    revision=self.revision)

    async def _call(self, service, data):
        async with asyncio.timeout(CALL_TIMEOUT):
            reply = await self.hass.services.async_call("home_control", service, data,
                                                       blocking=True, return_response=True)
        if not isinstance(reply, dict) or reply.get("accepted") is not True:
            raise ValueError("VOICE_REQUEST_REJECTED")
        return reply

    async def _pump(self):
        try:
            while not self.stopped:
                generation = self.generation
                metadata = self._metadata()
                desired = {} if self.maintenance else dict(self.current)
                if not self.registered or self.need_register or self.metadata != metadata:
                    reply = await self._call(REGISTER, dict(self._envelope(), **metadata,
                        active_issues=[dict(key=key, **value) for key, value in sorted(desired.items())]))
                    if generation != self.generation:
                        continue
                    if (type(reply.get("protocol_version")) is not int or reply["protocol_version"] != 1
                            or not isinstance(reply.get("center_session"), str) or not reply["center_session"]
                            or type(reply.get("lease_seconds")) is not int or reply["lease_seconds"] != LEASE):
                        raise ValueError("VOICE_PROTOCOL_MISMATCH")
                    self.center_session = reply["center_session"]
                    self.registered = True
                    self.need_register = False
                    self.metadata = metadata
                    self.sent = desired
                    self.connection = "registered"
                    continue  # Recheck changes that happened while awaiting the service.
                removed = sorted(self.sent.keys() - desired.keys())
                changed = sorted(key for key in desired if self.sent.get(key) != desired[key])
                if removed:
                    key = removed[0]
                    notice = dict(key=key, level=self.sent[key]["level"], message="", active=False, resolved=True)
                elif changed:
                    key = changed[0]
                    notice = dict(key=key, **desired[key], active=True, resolved=False)
                elif self.infos and not self.maintenance:
                    key = next(iter(self.infos))
                    notice = self.infos.pop(key)
                else:
                    return
                await self._call(PUBLISH, dict(self._envelope(), center_session=self.center_session, event=notice))
                if generation != self.generation:
                    continue
                if notice["resolved"]:
                    self.sent.pop(key, None)
                elif notice["active"]:
                    self.sent[key] = desired[key]
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self.connection = "center_error"
            self.registered = False
            self.need_register = True
            self.infos.clear()
            self.retry_at = self.hass.loop.time() + RENEW
            _LOGGER.debug("Voice center unavailable (%s); equipment control continues", type(error).__name__)

    @callback
    def stop(self):
        if self.stopped:
            return
        self.stopped = True
        self.connection = "stopped"
        self.infos.clear()
        for cancel in self.unsubscribers:
            cancel()
        self.unsubscribers.clear()
        if self.task and not self.task.done():
            self.task.cancel()
        if self.registered and self.center_session:
            data = dict(self._envelope(), center_session=self.center_session)
            self.closing_task = self.hass.async_create_task(self._unregister(data), eager_start=False)
        self.registered = False

    async def _unregister(self, data):
        try:
            await self._call(UNREGISTER, data)
        except Exception:
            _LOGGER.debug("Voice unregister not acknowledged; receiver lease must expire")

    def diagnostics(self):
        return dict(protocol_version=1, process_id="climate", connection=self.connection,
                    registered=self.registered, maintenance=self.maintenance,
                    active_keys=sorted(self.current), preferences={key: self.runtime.data.get(key, False) for key in PREFERENCES})
