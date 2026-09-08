"""Image entity showing the last access photo (spec §15.2, §11.3)."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.image import ImageEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.util import dt as dt_util

from . import HikvisionAccessEntry
from .entity import HikvisionAccessEntity
from .gateway import EventGateway, signal_access
from .models import AccessEvent


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    if not rt.capabilities.event_picture and not rt.capabilities.user_picture:
        return
    async_add_entities(
        [HikvisionLastAccessImage(hass, entry.entry_id, rt.info, rt.gateway)]
    )


class HikvisionLastAccessImage(HikvisionAccessEntity, ImageEntity):
    _attr_translation_key = "last_access"

    def __init__(
        self, hass: HomeAssistant, entry_id: str, info, gateway: EventGateway
    ) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        ImageEntity.__init__(self, hass)
        self._gateway = gateway
        self._attr_unique_id = f"{self._base_unique_id}_last_access"
        self._path: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, signal_access(self._entry_id), self._handle
            )
        )
        last = self._gateway.last_access_event
        if last:
            self._apply(last)

    @callback
    def _handle(self, event: AccessEvent) -> None:
        self._apply(event)
        self.async_write_ha_state()

    def _apply(self, event: AccessEvent) -> None:
        path = event.event_picture_path or event.user_picture_path
        if path and path != self._path:
            self._path = path
            self._attr_image_last_updated = dt_util.utcnow()

    async def async_image(self) -> bytes | None:
        if not self._path:
            return None

        def _read() -> bytes | None:
            p = Path(self._path)
            return p.read_bytes() if p.is_file() else None

        return await self.hass.async_add_executor_job(_read)
