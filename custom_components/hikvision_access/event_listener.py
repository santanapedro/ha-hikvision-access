"""Persistent alertStream listener (spec §9).

This firmware replays its *entire* historical log on connect (``currentEvent``
= ``false``), so the listener only acts on ``currentEvent == true`` — the
historical events are the reconciler's job. On every (re)connect it asks the
reconciler to run, to catch anything that happened while disconnected.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable

from homeassistant.core import HomeAssistant

from .api import HikvisionISAPIClient
from .const import EVENT_IDLE_TIMEOUT_S, RECONNECT_BACKOFF_S
from .event_parser import parse_boundary, parse_stream_envelope, split_stream_buffer
from .exceptions import HikvisionAuthError, HikvisionError, HikvisionLockoutError
from .gateway import EventGateway

_LOGGER = logging.getLogger(__name__)

_MAX_BUFFER = 4_000_000  # guard against a boundary we never match


class EventListener:
    def __init__(
        self,
        hass: HomeAssistant,
        client: HikvisionISAPIClient,
        gateway: EventGateway,
        device_serial: str,
        device_id: str,
        endpoint: str,
        *,
        door_name: str | None = None,
        on_reconnect: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self.hass = hass
        self._client = client
        self._gateway = gateway
        self._device_serial = device_serial
        self._device_id = device_id
        self._endpoint = endpoint
        self._door_name = door_name
        self._on_reconnect = on_reconnect
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._connected_once = False

    def start(self) -> None:
        self._stop.clear()
        self._task = self.hass.async_create_background_task(
            self._run(), name=f"hikvision_access listener {self._device_id}"
        )

    async def async_stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._gateway.listener_state = "disconnected"

    async def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            self._gateway.listener_state = "connecting" if attempt == 0 else "reconnecting"
            try:
                await self._connect_and_read()
                attempt = 0
            except HikvisionLockoutError as err:
                self._fail(err, level=logging.WARNING)
                await self._sleep(min(err.unlock_seconds or 120, 300))
            except HikvisionAuthError as err:
                self._fail(err, level=logging.ERROR)
                self._gateway.listener_state = "error"
                await self._sleep(60)
            except (HikvisionError, asyncio.TimeoutError, OSError) as err:
                self._fail(err)
                delay = RECONNECT_BACKOFF_S[min(attempt, len(RECONNECT_BACKOFF_S) - 1)]
                attempt += 1
                await self._sleep(delay)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                _LOGGER.exception("listener crashed for %s", self._device_id)
                await self._sleep(30)

    async def _connect_and_read(self) -> None:
        async with self._client.async_stream(
            self._endpoint, EVENT_IDLE_TIMEOUT_S
        ) as resp:
            boundary = parse_boundary(resp.headers.get("Content-Type", "")) or "MIME_boundary"
            self._gateway.listener_state = "connected"
            if self._connected_once:
                self._gateway.reconnects += 1
            self._connected_once = True
            if self._on_reconnect:
                self.hass.async_create_task(self._on_reconnect())

            buffer = b""
            async for chunk in resp.content.iter_any():
                if self._stop.is_set():
                    return
                buffer += chunk
                if len(buffer) > _MAX_BUFFER:
                    buffer = buffer[-_MAX_BUFFER:]
                sections, buffer = split_stream_buffer(buffer, boundary)
                for headers, content in sections:
                    await self._dispatch_section(headers, content)

    async def _dispatch_section(self, headers: dict[str, str], content: bytes) -> None:
        ctype = headers.get("content-type", "")
        if "json" not in ctype and content[:1] not in (b"{", b"["):
            return
        try:
            envelope = json.loads(content.decode("utf-8", errors="replace"))
        except json.JSONDecodeError:
            _LOGGER.debug("skipping unparseable stream section (%d bytes)", len(content))
            return

        ace = envelope.get("AccessControllerEvent", {})
        if ace.get("currentEvent") is not True:
            return  # historical replay — reconciler handles it

        event = parse_stream_envelope(
            envelope,
            device_serial=self._device_serial,
            device_id=self._device_id,
            door_name=self._door_name,
            mask_card=self._gateway.mask_card,
        )
        if event is None:
            return
        try:
            await self._gateway.async_handle(event, source="stream")
        except Exception:  # noqa: BLE001
            _LOGGER.exception("failed handling live event %s", event.event_uid)

    def _fail(self, err: object, level: int = logging.DEBUG) -> None:
        self._gateway.last_error = str(err)
        _LOGGER.log(level, "alertStream listener for %s: %s", self._device_id, err)

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
