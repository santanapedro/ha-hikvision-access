"""Access EventEntity (spec §15.3)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessEntry
from .const import (
    EVENT_ACCESS_DENIED,
    EVENT_ACCESS_GRANTED,
    EVENT_DEVICE_ALARM,
    EVENT_DOOR_CLOSED,
    EVENT_DOOR_OPENED,
    EVENT_TAMPER,
)
from .entity import HikvisionAccessEntity
from .event_mapper import EventMapping
from .gateway import signal_event
from .models import AccessEvent

EVENT_TYPES = [
    EVENT_ACCESS_GRANTED,
    EVENT_ACCESS_DENIED,
    EVENT_DOOR_OPENED,
    EVENT_DOOR_CLOSED,
    EVENT_TAMPER,
    EVENT_DEVICE_ALARM,
]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities([HikvisionAccessEvent(entry.entry_id, entry.runtime_data.info)])


class HikvisionAccessEvent(HikvisionAccessEntity, EventEntity):
    _attr_translation_key = "access"
    _attr_event_types = EVENT_TYPES

    def __init__(self, entry_id: str, info) -> None:
        super().__init__(entry_id, info)
        self._attr_unique_id = f"{self._base_unique_id}_access"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_event(self._entry_id), self._handle
            )
        )

    @callback
    def _handle(self, event: AccessEvent, mapping: EventMapping) -> None:
        if mapping.entity_event not in EVENT_TYPES:
            return
        self._trigger_event(
            mapping.entity_event,
            {
                "person_id": event.person_id,
                "person_name": event.person_name,
                "timestamp": event.timestamp.isoformat(),
                "door": event.door_name or event.door_id,
                "method": event.authentication_method,
                "result": event.access_result,
                "event_uid": event.event_uid,
                "has_picture": bool(event.event_picture_path),
                "live": bool(event.is_live),
            },
        )
        self.async_write_ha_state()
