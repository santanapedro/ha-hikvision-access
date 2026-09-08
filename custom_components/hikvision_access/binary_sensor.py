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
from .coordinator import HealthData, HikvisionHealthCoordinator
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
    async_add_entities(
        HikvisionBinarySensor(entry.entry_id, rt.info, rt.health, desc)
        for desc in DESCRIPTIONS
    )


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
