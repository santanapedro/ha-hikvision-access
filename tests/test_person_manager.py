"""PersonManager: cache hit, lookup, and negative caching."""

from __future__ import annotations

import pytest
from hikvision_access.person_manager import PersonManager


class _FakeStore:
    def __init__(self) -> None:
        self.rows: dict[tuple[str, str], dict] = {}

    async def async_get_person(self, device_id, person_id):
        return self.rows.get((device_id, person_id))

    async def async_upsert_person(self, device_id, person_id, name, picture_path):
        self.rows[(device_id, person_id)] = {
            "person_name": name,
            "picture_path": picture_path,
            "updated_at": "2999-01-01T00:00:00+00:00",  # always "fresh"
        }


class _FakeClient:
    def __init__(self, users: list[dict]) -> None:
        self._users = users
        self.calls = 0

    async def async_search_users(self, *_a, **_kw):
        self.calls += 1
        return {"UserInfo": self._users}


async def test_resolves_and_caches():
    store = _FakeStore()
    client = _FakeClient([{"employeeNo": "7", "name": "PEDRO"}])
    pm = PersonManager(client, store, "dev1")

    p = await pm.async_resolve("7")
    assert p["person_name"] == "PEDRO"
    assert client.calls == 1

    # second call is served from the store, no new lookup
    await pm.async_resolve("7")
    assert client.calls == 1


async def test_negative_cache_stops_repeated_lookups():
    store = _FakeStore()
    client = _FakeClient([])  # terminal doesn't know this employeeNo
    pm = PersonManager(client, store, "dev1")

    assert await pm.async_resolve("999") is None
    assert await pm.async_resolve("999") is None
    assert await pm.async_resolve("999") is None
    assert client.calls == 1  # only the first attempt hit the terminal


async def test_negative_cache_expires(monkeypatch):
    store = _FakeStore()
    client = _FakeClient([])
    pm = PersonManager(client, store, "dev1")

    import hikvision_access.person_manager as mod

    t = [1000.0]
    monkeypatch.setattr(mod.time, "monotonic", lambda: t[0])

    await pm.async_resolve("999")
    assert client.calls == 1

    t[0] += mod._NEG_TTL_S + 1  # past the negative-cache window
    await pm.async_resolve("999")
    assert client.calls == 2


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
