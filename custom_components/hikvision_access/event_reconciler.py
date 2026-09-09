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
_MAX_PAGES_PER_RUN = 40      # ~1200 events/run; more runs catch up gently
_PAGE_PAUSE_S = 0.5          # breathe between pages so we don't flood the terminal
_FIRST_RUN_LOOKBACK_DAYS = 7
_MISSING_URL_LOOKBACK_DAYS = 2   # how far back to chase photos the stream missed


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

        # A face access seen live has no pictureURL (the alertStream never
        # carries one). The fixed overlap above is quickly outrun by the door
        # relay/contact serials a single access emits, so also walk back to the
        # oldest recent decision that still lacks a photo.
        since_iso = (now - timedelta(days=_MISSING_URL_LOOKBACK_DAYS)).isoformat()
        missing_from = await self._store.async_oldest_missing_url_serial(
            self._device_id, since_iso
        )
        if missing_from is not None:
            begin_serial = (
                missing_from if begin_serial is None else min(begin_serial, missing_from)
            )

        start_iso = (now - timedelta(days=_FIRST_RUN_LOOKBACK_DAYS)).isoformat()
        end_iso = (now + timedelta(minutes=1)).isoformat()

        search_id = str(uuid.uuid4())
        position = 0
        new_count = 0
        highest = max_serial or 0

        for page_no in range(_MAX_PAGES_PER_RUN):
            if page_no:
                await asyncio.sleep(_PAGE_PAUSE_S)
            try:
                page = await self._client.async_search_acs_events(
                    search_id,
                    start_iso,
                    end_iso,
                    position=position,
                    max_results=ACS_EVENT_MAX_RESULTS,
                    begin_serial_no=begin_serial,
                )
            except HikvisionError as err:
                # keep whatever we imported; next run resumes from max serial
                _LOGGER.debug("reconcile paused at page %d: %s", page_no, err)
                break
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
                try:
                    if await self._gateway.async_handle(event, source="reconcile"):
                        new_count += 1
                except HikvisionError:
                    pass  # e.g. a picture fetch hit the rate limit; keep going
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
        await self._backfill_pictures()
        await self._store.async_set_meta("last_reconcile", dt_util.utcnow().isoformat())
        return new_count

    async def _backfill_pictures(self, batch: int = 15) -> None:
        """Download a few event photos that were skipped earlier (rate limit)."""
        images = self._gateway.images
        if images is None:
            return
        rows = await self._store.async_events_missing_pictures(self._device_id, batch)
        if not rows:
            return
        done = 0
        for row in rows:
            await asyncio.sleep(_PAGE_PAUSE_S)
            try:
                path = await images.async_fetch_event_image_url(
                    row["event_uid"], row["event_picture_url"], row["timestamp"]
                )
            except HikvisionError as err:
                _LOGGER.debug("picture backfill paused: %s", err)
                break  # locked / rate-limited — try again next run
            if path:
                await self._store.async_update_event_fields(
                    row["event_uid"], event_picture_path=path
                )
                done += 1
        _LOGGER.info(
            "picture backfill for %s: %d/%d done (%d still pending)",
            self._device_id, done, len(rows),
            len(rows) - done if done < len(rows) else 0,
        )
