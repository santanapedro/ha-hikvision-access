"""Access EventEntity (spec §15.3)."""

from __future__ import annotations

from homeassistant.components.event import EventEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionAccessEntry
from .const import (
    EVENT_ACCESS_DENIED,
    EVENT_ACCESS_GRANTED,
    EVENT_BUS_EVENT,
    EVENT_DEVICE_ALARM,
    EVENT_DOOR_CLOSED,
    EVENT_DOOR_OPENED,
    EVENT_TAMPER,
)
from .coordinator import HikvisionCallCoordinator
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


CALL_EVENT_TYPES = ["ring", "answered", "ended"]


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    entities: list = [HikvisionAccessEvent(entry.entry_id, rt.info)]
    if rt.call is not None:
        entities.append(HikvisionCallEvent(entry.entry_id, rt.info, rt.call))
    async_add_entities(entities)


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


class HikvisionCallEvent(
    HikvisionAccessEntity, CoordinatorEntity[HikvisionCallCoordinator], EventEntity
):
    """Fires when the call (doorbell) button is pressed and on call transitions."""

    _attr_translation_key = "call"
    _attr_event_types = CALL_EVENT_TYPES

    def __init__(self, entry_id: str, info, coordinator: HikvisionCallCoordinator) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        CoordinatorEntity.__init__(self, coordinator)
        self._attr_unique_id = f"{self._base_unique_id}_call"
        self._prev = "idle"

    @callback
    def _handle_coordinator_update(self) -> None:
        status = self.coordinator.data or "idle"
        was, self._prev = self._prev, status
        if status == was:
            return
        etype = None
        if self.coordinator.is_ringing:
            etype = "ring"
        elif self.coordinator.in_call:
            etype = "answered"
        elif was != "idle" and status == "idle":
            etype = "ended"
        if etype is None:
            return
        self._trigger_event(etype, {"call_status": status})
        self.async_write_ha_state()
        self.hass.bus.async_fire(
            EVENT_BUS_EVENT,
            {
                "entry_id": self._entry_id,
                "device_name": self._info.device_name or self._info.model,
                "result": "call",
                "method": "button",
                "call_event": etype,
            },
        )
