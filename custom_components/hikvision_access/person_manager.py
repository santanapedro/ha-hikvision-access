"""Resolve ``employeeNo`` -> name / enrolled photo, with a local cache (spec §14).

Cache TTL is 24 h; a miss (or a cached row still missing the name) triggers a
``UserInfo/Search`` by employee number.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

from .api import HikvisionISAPIClient
from .exceptions import HikvisionError

_LOGGER = logging.getLogger(__name__)

_TTL = timedelta(hours=24)
_NEG_TTL_S = 3600  # don't re-hit the terminal for an employeeNo it doesn't know


class PersonManager:
    def __init__(
        self,
        client: HikvisionISAPIClient,
        store,
        device_id: str,
        images=None,
    ) -> None:
        self._client = client
        self._store = store
        self._device_id = device_id
        self._images = images
        self._inflight: set[str] = set()
        self._negative: dict[str, float] = {}

    async def async_resolve(self, person_id: str) -> dict | None:
        cached = await self._store.async_get_person(self._device_id, person_id)
        if cached and cached.get("person_name") and _fresh(cached.get("updated_at")):
            return cached

        if person_id in self._inflight:
            return cached
        deadline = self._negative.get(person_id)
        if deadline and deadline > time.monotonic():
            return cached
        self._inflight.add(person_id)
        try:
            info = await self._fetch(person_id)
        except HikvisionError as err:
            _LOGGER.debug("UserInfo lookup for %s failed: %s", person_id, err)
            return cached
        finally:
            self._inflight.discard(person_id)

        if not info:
            self._negative[person_id] = time.monotonic() + _NEG_TTL_S
            return cached
        self._negative.pop(person_id, None)

        name = info.get("name")
        picture_path = None
        face_url = info.get("faceURL")
        if face_url and self._images is not None:
            picture_path = await self._images.async_fetch_user_image(
                person_id, face_url
            )

        await self._store.async_upsert_person(
            self._device_id, person_id, name, picture_path
        )
        return await self._store.async_get_person(self._device_id, person_id)

    async def _fetch(self, person_id: str) -> dict | None:
        result = await self._client.async_search_users(
            str(uuid.uuid4())[:32], employee_nos=[person_id], max_results=1
        )
        users = result.get("UserInfo") or []
        for user in users:
            if str(user.get("employeeNo")) == str(person_id):
                return user
        return users[0] if users else None

    async def async_prime(self) -> int:
        """Load every enrolled user into the cache at startup (small terminals)."""
        loaded = 0
        position = 0
        sid = str(uuid.uuid4())[:32]
        while True:
            try:
                res = await self._client.async_search_users(
                    sid, position=position, max_results=30
                )
            except HikvisionError as err:
                _LOGGER.debug("user prime stopped at %d: %s", position, err)
                break
            users = res.get("UserInfo") or []
            for user in users:
                pid = str(user.get("employeeNo") or "").strip()
                if not pid:
                    continue
                await self._store.async_upsert_person(
                    self._device_id, pid, user.get("name"), None
                )
                loaded += 1
            position += len(users)
            if res.get("responseStatusStrg") != "MORE" or not users:
                break
        return loaded


def _fresh(updated_at: str | None) -> bool:
    if not updated_at:
        return False
    try:
        ts = datetime.fromisoformat(updated_at)
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return datetime.now(UTC) - ts.astimezone(UTC) < _TTL
