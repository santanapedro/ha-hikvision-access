"""Live view camera for the terminal (RTSP + ISAPI snapshot)."""

from __future__ import annotations

import logging

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import HikvisionAccessEntry
from .const import CALL_CHANNEL_MAIN, DEFAULT_RTSP_PORT, OPT_ENABLE_CAMERA, OPT_RTSP_PORT
from .entity import HikvisionAccessEntity
from .exceptions import HikvisionError

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HikvisionAccessEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    rt = entry.runtime_data
    if not rt.capabilities.video or not entry.options.get(OPT_ENABLE_CAMERA, True):
        return
    port = int(entry.options.get(OPT_RTSP_PORT, DEFAULT_RTSP_PORT))
    async_add_entities([HikvisionAccessCamera(entry.entry_id, rt.info, rt.client, port)])


class HikvisionAccessCamera(HikvisionAccessEntity, Camera):
    _attr_translation_key = "live"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry_id: str, info, client, rtsp_port: int) -> None:
        HikvisionAccessEntity.__init__(self, entry_id, info)
        Camera.__init__(self)
        self._client = client
        self._rtsp_port = rtsp_port
        self._attr_unique_id = f"{self._base_unique_id}_camera"

    async def stream_source(self) -> str | None:
        return self._client.rtsp_url(CALL_CHANNEL_MAIN, self._rtsp_port)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self._client.async_get_snapshot(CALL_CHANNEL_MAIN)
        except HikvisionError as err:
            _LOGGER.debug("snapshot failed: %s", err)
            return None
