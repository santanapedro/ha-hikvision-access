"""Binary sensors: connectivity, door contact, tamper (spec §16)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import HikvisionAccessEntry
from .coordinator import (
    HealthData,
    HikvisionCallCoordinator,
    HikvisionHealthCoordinator,
)
from .entity import HikvisionAccessEntity


@dataclass(frozen=True, kw_only=True)
class HikBinarySensorDescription(BinarySensorEntityDescription):
    value_fn: Callable[[HealthData], bool | None]


DESCRIPTIONS: tuple[HikBinarySensorDescription, ...] = (
    HikBinarySensorDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda d: d.reachable,
    ),
    HikBinarySensorDescription(
        key="door",
        translation_key="door",
        device_class=BinarySensorDeviceClass.DOOR,
        value_fn=lambda d: d.door_open,
    ),
    HikBinarySensorDescription(
        key="tamper",
        translation_key="tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        value_fn=lambda d: d.tamper,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    entities: list = [
        HikvisionBinarySensor(entry.entry_id, rt.info, rt.health, desc)
        for desc in DESCRIPTIONS
    ]
    if rt.call is not None:
        entities.append(
            HikvisionDoorbellSensor(entry.entry_id, rt.info, rt.call)
        )
    async_add_entities(entities)


class HikvisionBinarySensor(
    HikvisionAccessEntity, CoordinatorEntity[HikvisionHealthCoordinator], BinarySensorEntity
):
    entity_description: HikBinarySensorDescription

    def __init__(self, entry_id, info, coordinator, description) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        CoordinatorEntity.__init__(self, coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{self._base_unique_id}_{description.key}"

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def available(self) -> bool:
        if self.entity_description.key == "online":
            return True
        return super().available and self.coordinator.data.reachable


class HikvisionDoorbellSensor(
    HikvisionAccessEntity,
    CoordinatorEntity[HikvisionCallCoordinator],
    BinarySensorEntity,
):
    """On while the call (doorbell) button is ringing or on an active call."""

    _attr_translation_key = "doorbell"
    _attr_device_class = BinarySensorDeviceClass.SOUND

    def __init__(self, entry_id, info, coordinator: HikvisionCallCoordinator) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        CoordinatorEntity.__init__(self, coordinator)
        self._attr_unique_id = f"{self._base_unique_id}_doorbell"

    @property
    def is_on(self) -> bool:
        return self.coordinator.is_ringing or self.coordinator.in_call

    @property
    def extra_state_attributes(self) -> dict:
        return {"call_status": self.coordinator.data}
