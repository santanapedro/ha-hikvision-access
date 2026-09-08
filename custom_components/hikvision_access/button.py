"""Open-door button (spec §17, §30).

Physical action. Created only when the option is enabled and the terminal
reports remote-door support. Never held open; a press sends a single
momentary ``open`` and the terminal's own ``openDuration`` re-locks it.
"""

from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessEntry
from .const import OPT_CREATE_OPEN_DOOR_BUTTON
from .entity import HikvisionAccessEntity
from .exceptions import HikvisionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    if not entry.options.get(OPT_CREATE_OPEN_DOOR_BUTTON, True):
        return
    if not rt.capabilities.remote_door_control:
        return
    async_add_entities(
        HikvisionOpenDoorButton(entry.entry_id, rt.info, rt.client, door)
        for door in range(1, rt.capabilities.door_count + 1)
    )


class HikvisionOpenDoorButton(HikvisionAccessEntity, ButtonEntity):
    _attr_translation_key = "open_door"

    def __init__(self, entry_id, info, client, door: int) -> None:
        super().__init__(entry_id, info)
        self._client = client
        self._door = door
        suffix = f"_{door}" if door > 1 else ""
        self._attr_unique_id = f"{self._base_unique_id}_open_door{suffix}"
        self._attr_translation_placeholders = {"door": str(door)}

    async def async_press(self) -> None:
        _LOGGER.info(
            "Remote open: terminal %s door %s (requested from Home Assistant)",
            self._info.serial_number,
            self._door,
        )
        try:
            await self._client.async_remote_door(self._door, "open")
        except HikvisionError as err:
            raise HomeAssistantError(f"Falha ao abrir a porta: {err}") from err
