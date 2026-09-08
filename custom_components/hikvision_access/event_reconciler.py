"""Periodic reconciliation against ``AcsEvent`` (spec §10).

Guarantees every event stored on the terminal reaches Home Assistant even if
the realtime path missed it. Walks forward from the highest ``serialNo`` we
already have (minus an overlap), pages through, and feeds each item to the
gateway — dedupe there drops anything already seen.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.util import dt as dt_util

from .api import HikvisionISAPIClient
from .const import ACS_EVENT_MAX_RESULTS
from .event_parser import parse_acs_search_item
from .exceptions import HikvisionError
from .gateway import EventGateway

_LOGGER = logging.getLogger(__name__)

_SERIAL_OVERLAP = 5          # re-fetch the last few serials each run
_MAX_PAGES_PER_RUN = 200     # safety cap (~6000 events)
_FIRST_RUN_LOOKBACK_DAYS = 7


class EventReconciler:
    def __init__(
        self,
        hass: HomeAssistant,
        client: HikvisionISAPIClient,
        store,
        gateway: EventGateway,
        device_serial: str,
        device_id: str,
        interval_s: int,
        *,
        door_name: str | None = None,
    ) -> None:
        self.hass = hass
        self._client = client
        self._store = store
        self._gateway = gateway
        self._device_serial = device_serial
        self._device_id = device_id
        self._interval = timedelta(seconds=interval_s)
        self._door_name = door_name
        self._unsub = None
        self._running = asyncio.Lock()

    async def async_start(self) -> None:
        # first sweep runs in the background so it never delays entry setup
        # (an empty DB can pull thousands of historical events)
        self.hass.async_create_background_task(
            self.async_run_once(), name="hikvision_access first reconcile"
        )
        self._unsub = async_track_time_interval(
            self.hass, self._scheduled, self._interval
        )

    def async_stop(self) -> None:
        if self._unsub:
            self._unsub()
            self._unsub = None

    async def _scheduled(self, _now) -> None:
        await self.async_run_once()

    async def async_run_once(self) -> int:
        if self._running.locked():
            return 0
        async with self._running:
            try:
                return await self._reconcile()
            except HikvisionError as err:
                self._gateway.last_error = str(err)
                _LOGGER.debug("reconciliation failed: %s", err)
                return 0

    async def _reconcile(self) -> int:
        max_serial = await self._store.async_max_serial(self._device_id)
        begin_serial = None
        if max_serial is not None:
            begin_serial = max(1, max_serial - _SERIAL_OVERLAP + 1)

        now = dt_util.now().replace(microsecond=0)
        start_iso = (now - timedelta(days=_FIRST_RUN_LOOKBACK_DAYS)).isoformat()
        end_iso = (now + timedelta(minutes=1)).isoformat()

        search_id = str(uuid.uuid4())
        position = 0
        new_count = 0
        highest = max_serial or 0

        for _ in range(_MAX_PAGES_PER_RUN):
            page = await self._client.async_search_acs_events(
                search_id,
                start_iso,
                end_iso,
                position=position,
                max_results=ACS_EVENT_MAX_RESULTS,
                begin_serial_no=begin_serial,
            )
            infos = page.get("InfoList") or []
            if not infos:
                break
            for item in infos:
                event = parse_acs_search_item(
                    item,
                    device_serial=self._device_serial,
                    device_id=self._device_id,
                    door_name=self._door_name,
                    mask_card=self._gateway.mask_card,
                )
                if await self._gateway.async_handle(event, source="reconcile"):
                    new_count += 1
                with contextlib.suppress(TypeError, ValueError):
                    highest = max(highest, int(item.get("serialNo", 0)))
            position += len(infos)
            if page.get("responseStatusStrg") != "MORE":
                break

        if new_count:
            _LOGGER.info(
                "reconciled %d new event(s) for %s (serial now %d)",
                new_count,
                self._device_id,
                highest,
            )
        await self._store.async_set_meta("last_reconcile", dt_util.utcnow().isoformat())
        return new_count
