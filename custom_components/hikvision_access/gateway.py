"""Single funnel every access event passes through, whatever its source.

stream listener / push view / reconciler  ->  EventGateway.async_handle()
    -> dedupe in SQLite
    -> enrich (person name, event picture)
    -> update "last access" state
    -> notify entities (dispatcher) + fire the HA bus event (spec §15.4)
"""

from __future__ import annotations

import logging

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import (
    DOMAIN,
    EVENT_BUS_EVENT,
    OPT_MASK_CARD_NUMBER,
    OPT_STORE_DENIED_IMAGES,
    OPT_STORE_GRANTED_IMAGES,
    OPT_STORE_RAW_PAYLOAD,
    RESULT_DENIED,
    RESULT_GRANTED,
)
from .event_mapper import map_event
from .models import AccessEvent

_LOGGER = logging.getLogger(__name__)


def signal_event(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_event"


def signal_access(entry_id: str) -> str:
    return f"{DOMAIN}_{entry_id}_access"


class EventGateway:
    def __init__(
        self,
        hass: HomeAssistant,
        entry_id: str,
        device_serial: str,
        device_name: str,
        store,
        images,
        persons,
        options: dict,
    ) -> None:
        self.hass = hass
        self.entry_id = entry_id
        self.device_serial = device_serial
        self.device_name = device_name
        self._store = store
        self._images = images
        self._persons = persons
        self._options = options

        self.last_event: AccessEvent | None = None
        self.last_access_event: AccessEvent | None = None
        self.reconnects = 0
        self.listener_state = "init"
        self.last_error: str | None = None

    def update_options(self, options: dict) -> None:
        self._options = options

    @property
    def images(self):
        return self._images

    async def async_restore(self) -> None:
        """Seed 'last access' state from the DB so sensors survive a restart."""
        row = await self._store.async_latest_event(self.device_serial)
        if not row:
            return
        try:
            ts = dt_util.parse_datetime(row["timestamp"])
        except (KeyError, TypeError, ValueError):
            return
        event = AccessEvent(
            event_uid=row["event_uid"],
            device_id=row["device_id"],
            timestamp=ts or dt_util.utcnow(),
            serial_number=row.get("serial_number"),
            door_id=row.get("door_id"),
            door_name=row.get("door_name"),
            person_id=row.get("person_id"),
            person_name=row.get("person_name"),
            card_number=row.get("card_number"),
            authentication_method=row.get("authentication_method"),
            access_result=row.get("access_result") or "unknown",
            major_event_type=row.get("major_event_type"),
            minor_event_type=row.get("minor_event_type"),
            event_picture_path=row.get("event_picture_path"),
            user_picture_path=row.get("user_picture_path"),
            is_live=False,
        )
        self.last_event = event
        if map_event(event.major_event_type, event.minor_event_type).is_access_decision:
            self.last_access_event = event

    async def async_handle(self, event: AccessEvent, *, source: str) -> bool:
        """Process one event. Returns True if it was new."""
        if not self._options.get(OPT_STORE_RAW_PAYLOAD, False):
            event.raw_payload = None

        is_new = await self._store.async_insert_event(event)
        if not is_new:
            return False

        _LOGGER.debug(
            "event %s.%s %s person=%s result=%s (%s)",
            event.major_event_type,
            event.minor_event_type,
            event.event_uid,
            event.person_id,
            event.access_result,
            source,
        )

        await self._enrich(event)

        self.last_event = event
        mapping = map_event(event.major_event_type, event.minor_event_type)
        if mapping.is_access_decision:
            self.last_access_event = event

        async_dispatcher_send(self.hass, signal_event(self.entry_id), event, mapping)
        if mapping.is_access_decision:
            async_dispatcher_send(self.hass, signal_access(self.entry_id), event)

        self.hass.bus.async_fire(
            EVENT_BUS_EVENT,
            {
                "entry_id": self.entry_id,
                "device_id": event.device_id,
                "device_name": self.device_name,
                "event_uid": event.event_uid,
                "person_id": event.person_id,
                "person_name": event.person_name,
                "result": event.access_result,
                "method": event.authentication_method,
                "door": event.door_name or event.door_id,
                "timestamp": event.timestamp.isoformat(),
                "has_picture": bool(event.event_picture_path),
                "live": bool(event.is_live),
                "source": source,
            },
        )
        return True

    async def _enrich(self, event: AccessEvent) -> None:
        if event.person_id and not event.person_name and self._persons is not None:
            person = await self._persons.async_resolve(event.person_id)
            if person:
                event.person_name = person.get("person_name") or event.person_name
                event.user_picture_path = person.get("picture_path")

        if event.event_picture_url and self._images is not None and self._want_image(
            event.access_result
        ):
            path = await self._images.async_fetch_event_image(event)
            if path:
                event.event_picture_path = path

        updates: dict = {}
        if event.person_name:
            updates["person_name"] = event.person_name
        if event.event_picture_path:
            updates["event_picture_path"] = event.event_picture_path
        if event.user_picture_path:
            updates["user_picture_path"] = event.user_picture_path
        if updates:
            await self._store.async_update_event_fields(event.event_uid, **updates)

    def _want_image(self, result: str) -> bool:
        if result == RESULT_GRANTED:
            return self._options.get(OPT_STORE_GRANTED_IMAGES, True)
        if result == RESULT_DENIED:
            return self._options.get(OPT_STORE_DENIED_IMAGES, True)
        return self._options.get(OPT_STORE_GRANTED_IMAGES, True)

    @property
    def mask_card(self) -> bool:
        return self._options.get(OPT_MASK_CARD_NUMBER, True)

    def health_snapshot(self) -> dict:
        return {
            "listener_state": self.listener_state,
            "listener_reconnect_count": self.reconnects,
            "listener_last_error": self.last_error,
            "last_event": self.last_event.timestamp.isoformat()
            if self.last_event
            else None,
        }
