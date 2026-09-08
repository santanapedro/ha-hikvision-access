"""Sensors: connection status, firmware, and last-access fields (spec §15.1)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionAccessEntry
from .const import (
    METHOD_BUTTON,
    METHOD_CARD,
    METHOD_FACE,
    METHOD_FINGERPRINT,
    METHOD_OTHER,
    METHOD_PASSWORD,
    METHOD_QR,
    METHOD_REMOTE,
    METHOD_UNKNOWN,
    RESULT_DENIED,
    RESULT_GRANTED,
    RESULT_UNKNOWN,
)
from .coordinator import HikvisionHealthCoordinator
from .entity import HikvisionAccessEntity
from .gateway import EventGateway, signal_access
from .models import AccessEvent

CONNECTION_STATES = ["connected", "degraded", "offline", "authentication_error"]
METHOD_STATES = [
    METHOD_FACE, METHOD_CARD, METHOD_FINGERPRINT, METHOD_QR, METHOD_PASSWORD,
    METHOD_REMOTE, METHOD_BUTTON, METHOD_OTHER, METHOD_UNKNOWN,
]
RESULT_STATES = [RESULT_GRANTED, RESULT_DENIED, RESULT_UNKNOWN]


@dataclass(frozen=True, kw_only=True)
class LastAccessDescription(SensorEntityDescription):
    value_fn: Callable[[AccessEvent], str | datetime | None]


LAST_ACCESS: tuple[LastAccessDescription, ...] = (
    LastAccessDescription(
        key="last_access_user",
        translation_key="last_access_user",
        value_fn=lambda e: e.person_name or e.person_id,
    ),
    LastAccessDescription(
        key="last_access_time",
        translation_key="last_access_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda e: e.timestamp,
    ),
    LastAccessDescription(
        key="last_access_result",
        translation_key="last_access_result",
        device_class=SensorDeviceClass.ENUM,
        options=RESULT_STATES,
        value_fn=lambda e: e.access_result,
    ),
    LastAccessDescription(
        key="last_auth_method",
        translation_key="last_auth_method",
        device_class=SensorDeviceClass.ENUM,
        options=METHOD_STATES,
        value_fn=lambda e: e.authentication_method,
    ),
    LastAccessDescription(
        key="last_door",
        translation_key="last_door",
        value_fn=lambda e: e.door_name or e.door_id,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    entities: list[SensorEntity] = [
        HikvisionConnectionSensor(entry.entry_id, rt.info, rt.health, rt.gateway),
        HikvisionFirmwareSensor(entry.entry_id, rt.info, rt.health),
        HikvisionEventsTodaySensor(entry.entry_id, rt.info, rt.gateway, rt.store),
    ]
    entities += [
        HikvisionLastAccessSensor(entry.entry_id, rt.info, rt.gateway, desc)
        for desc in LAST_ACCESS
    ]
    async_add_entities(entities)


class _HealthBase(
    HikvisionAccessEntity, CoordinatorEntity[HikvisionHealthCoordinator], SensorEntity
):
    def __init__(self, entry_id, info, coordinator) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        CoordinatorEntity.__init__(self, coordinator)


class HikvisionConnectionSensor(_HealthBase):
    _attr_translation_key = "connection_status"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = CONNECTION_STATES
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, entry_id, info, coordinator, gateway: EventGateway) -> None:
        super().__init__(entry_id, info, coordinator)
        self._gateway = gateway
        self._attr_unique_id = f"{self._base_unique_id}_connection_status"

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_access(self._entry_id), lambda _e: self.async_write_ha_state()
            )
        )

    @property
    def native_value(self) -> str:
        if self._gateway.listener_state == "error":
            return "authentication_error"
        if not self.coordinator.last_update_success:
            return "offline"
        if self._gateway.listener_state in ("reconnecting", "connecting", "disconnected"):
            return "degraded"
        if self.coordinator.data and self.coordinator.data.net_status == "disconnect":
            return "degraded"
        return "connected"

    @property
    def extra_state_attributes(self) -> dict:
        return self._gateway.health_snapshot()


class HikvisionFirmwareSensor(_HealthBase):
    _attr_translation_key = "firmware"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, entry_id, info, coordinator) -> None:
        super().__init__(entry_id, info, coordinator)
        self._attr_unique_id = f"{self._base_unique_id}_firmware"
        self._attr_native_value = info.firmware


class HikvisionLastAccessSensor(HikvisionAccessEntity, SensorEntity):
    entity_description: LastAccessDescription

    def __init__(
        self, entry_id, info, gateway: EventGateway, description: LastAccessDescription
    ) -> None:
        super().__init__(entry_id, info)
        self.entity_description = description
        self._gateway = gateway
        self._attr_unique_id = f"{self._base_unique_id}_{description.key}"

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_access(self._entry_id), self._handle
            )
        )

    @callback
    def _handle(self, _event: AccessEvent) -> None:
        self.async_write_ha_state()

    @property
    def native_value(self):
        event = self._gateway.last_access_event
        if event is None:
            return None
        return self.entity_description.value_fn(event)


class HikvisionEventsTodaySensor(HikvisionAccessEntity, SensorEntity):
    _attr_translation_key = "events_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = "total"

    def __init__(self, entry_id, info, gateway: EventGateway, store) -> None:
        super().__init__(entry_id, info)
        self._gateway = gateway
        self._store = store
        self._attr_unique_id = f"{self._base_unique_id}_events_today"
        self._value = 0

    async def async_added_to_hass(self) -> None:
        await self._refresh()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_access(self._entry_id), lambda _e: self.hass.async_create_task(self._refresh())
            )
        )

    async def _refresh(self) -> None:
        from homeassistant.util import dt as dt_util

        start = dt_util.start_of_local_day()
        self._value = await self._store.async_count_today(
            self._info.serial_number, start.isoformat()
        )
        self.async_write_ha_state()

    @property
    def native_value(self) -> int:
        return self._value
