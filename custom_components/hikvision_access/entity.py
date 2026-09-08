"""Shared device wiring, as a mixin (no Entity base, to keep the MRO simple
when combined with CoordinatorEntity / platform entities)."""

from __future__ import annotations

from homeassistant.helpers.entity import DeviceInfo as HaDeviceInfo

from .const import DOMAIN
from .models import DeviceInfo


class HikvisionAccessEntity:
    """Attaches every entity to the terminal's device registry entry."""

    _attr_has_entity_name = True

    def __init__(self, entry_id: str, info: DeviceInfo) -> None:
        self._entry_id = entry_id
        self._info = info
        self._attr_device_info = HaDeviceInfo(
            identifiers={(DOMAIN, info.serial_number)},
            manufacturer="Hikvision",
            model=info.model,
            name=info.device_name or info.model,
            sw_version=info.firmware,
            serial_number=info.serial_number,
        )

    @property
    def _base_unique_id(self) -> str:
        return self._info.serial_number
