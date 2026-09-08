"""Download and retain event / user pictures (spec §11, §12, §28).

Images live under ``<config>/hikvision_access/<entry_id>/media/`` — never in
``/config/www`` (spec §11.4). They are served to the frontend only through the
authenticated HTTP view in ``http_views.py``.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from pathlib import Path

from homeassistant.util import dt as dt_util

from .api import HikvisionISAPIClient
from .exceptions import HikvisionError
from .models import AccessEvent

_LOGGER = logging.getLogger(__name__)


class ImageManager:
    def __init__(
        self, client: HikvisionISAPIClient, base_dir: Path, retention_days: int
    ) -> None:
        self._client = client
        self._base = base_dir
        self._retention_days = retention_days

    async def async_setup(self) -> None:
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: (self._base / "events").mkdir(parents=True, exist_ok=True)
        )
        await asyncio.get_running_loop().run_in_executor(
            None, lambda: (self._base / "users").mkdir(parents=True, exist_ok=True)
        )

    # ---- fetch ------------------------------------------------------

    async def async_fetch_event_image(self, event: AccessEvent) -> str | None:
        if not event.event_picture_url:
            return None
        month = event.timestamp.strftime("%Y%m")
        rel = Path("events") / month / f"{_safe(event.event_uid)}.jpg"
        return await self._download(event.event_picture_url, rel)

    async def async_fetch_user_image(self, person_id: str, url: str) -> str | None:
        rel = Path("users") / f"{_safe(person_id)}.jpg"
        return await self._download(url, rel)

    async def async_save_inline(self, event: AccessEvent, jpeg: bytes) -> str | None:
        """Persist a JPEG that arrived inline in a push multipart body."""
        if not jpeg or jpeg[:3] != b"\xff\xd8\xff":
            return None
        month = event.timestamp.strftime("%Y%m")
        dest = self._base / "events" / month / f"{_safe(event.event_uid)}.jpg"

        def _write() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(jpeg)

        await asyncio.get_running_loop().run_in_executor(None, _write)
        return str(dest)

    async def _download(self, url: str, rel: Path) -> str | None:
        dest = self._base / rel
        if await _exists(dest):
            return str(dest)
        try:
            blob = await self._client.async_get_bytes(url)
        except HikvisionError as err:
            _LOGGER.debug("picture download failed (%s): %s", url, err)
            return None
        if not blob or blob[:3] != b"\xff\xd8\xff":
            _LOGGER.debug("picture %s is not a JPEG (%d bytes)", url, len(blob))
            return None

        def _write() -> None:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)

        await asyncio.get_running_loop().run_in_executor(None, _write)
        return str(dest)

    # ---- retention -----------------------------------------------

    def update_retention(self, days: int) -> None:
        self._retention_days = days

    async def async_purge(self, store) -> int:
        if self._retention_days <= 0:
            return 0
        cutoff = dt_util.utcnow() - timedelta(days=self._retention_days)
        paths = await store.async_purge_images_before(cutoff.isoformat())

        def _unlink() -> int:
            removed = 0
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                    removed += 1
                except OSError:
                    pass
            # also sweep orphaned files on disk older than cutoff
            for f in self._base.rglob("*.jpg"):
                try:
                    if f.stat().st_mtime < cutoff.timestamp():
                        f.unlink(missing_ok=True)
                        removed += 1
                except OSError:
                    pass
            return removed

        return await asyncio.get_running_loop().run_in_executor(None, _unlink)


def _safe(value: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value)[:120]


async def _exists(path: Path) -> bool:
    return await asyncio.get_running_loop().run_in_executor(None, path.exists)
