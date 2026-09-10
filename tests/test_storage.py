"""SQLite store: dedupe, serial tracking, retention, queries."""

from datetime import UTC, datetime, timedelta

import pytest
from hikvision_access.models import AccessEvent
from hikvision_access.storage import EventStore, compute_event_uid


def _event(uid: str, serial: str | None, ts: datetime, **kw) -> AccessEvent:
    return AccessEvent(
        event_uid=uid, device_id="d1", timestamp=ts, serial_number=serial, **kw
    )


@pytest.fixture
async def store(tmp_path):
    s = EventStore(tmp_path / "t.db")
    await s.async_open()
    yield s
    await s.async_close()


async def test_dedupe(store):
    ts = datetime(2026, 9, 1, tzinfo=UTC)
    ev = _event("d1:100", "100", ts, access_result="granted")
    assert await store.async_insert_event(ev) is True
    assert await store.async_insert_event(ev) is False  # same uid


async def test_max_serial_tracks_highest(store):
    ts = datetime(2026, 9, 1, tzinfo=UTC)
    for s in (10, 5, 42, 7):
        await store.async_insert_event(_event(f"d1:{s}", str(s), ts))
    assert await store.async_max_serial("d1") == 42


async def test_purge_events_before_and_vacuum(store):
    old = datetime(2020, 1, 1, tzinfo=UTC)
    recent = datetime.now(UTC)
    for i in range(4):
        await store.async_insert_event(
            _event(f"d1:{i}", str(i), old + timedelta(seconds=i), access_result="granted")
        )
    await store.async_insert_event(
        _event("d1:99", "99", recent, access_result="granted")
    )

    cutoff = (datetime.now(UTC) - timedelta(days=365)).isoformat()
    removed = await store.async_purge_events_before(cutoff)
    assert removed == 4

    left = await store.async_query_events(device_id="d1")
    assert [r["event_uid"] for r in left] == ["d1:99"]

    await store.async_vacuum()  # must not raise
    assert (await store.async_query_events(device_id="d1"))[0]["event_uid"] == "d1:99"


async def test_oldest_missing_url_serial(store):
    now = datetime.now(UTC)
    since = (now - timedelta(days=2)).isoformat()
    # a live face access with no photo yet (serial 200) + its door cycle
    await store.async_insert_event(
        _event("d1:200", "200", now - timedelta(hours=1), person_id="7",
               access_result="granted", minor_event_type="75")
    )
    await store.async_insert_event(
        _event("d1:203", "203", now - timedelta(minutes=30), person_id="8",
               access_result="granted", minor_event_type="75")
    )
    # door events must be ignored even though they also lack a URL
    await store.async_insert_event(
        _event("d1:204", "204", now, access_result="unknown", minor_event_type="21")
    )
    # an old decision (outside the lookback window) must be ignored
    await store.async_insert_event(
        _event("d1:50", "50", now - timedelta(days=5), person_id="1",
               access_result="granted", minor_event_type="75")
    )
    assert await store.async_oldest_missing_url_serial("d1", since) == 200

    # once the URL lands, that serial drops out of the set
    await store.async_update_event_fields("d1:200", event_picture_url="http://x/a.jpg")
    assert await store.async_oldest_missing_url_serial("d1", since) == 203

    await store.async_update_event_fields("d1:203", event_picture_url="http://x/b.jpg")
    assert await store.async_oldest_missing_url_serial("d1", since) is None


async def test_access_only_excludes_door_events(store):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    # a granted face access + its door cycle (no person, unknown result)
    await store.async_insert_event(
        _event("d1:1", "1", base, person_id="7", access_result="granted",
               minor_event_type="75")
    )
    for i, minor in enumerate((21, 22, 23, 24), start=2):
        await store.async_insert_event(
            _event(f"d1:{i}", str(i), base + timedelta(seconds=i),
                   access_result="unknown", minor_event_type=str(minor))
        )
    all_rows = await store.async_query_events(device_id="d1")
    assert len(all_rows) == 5
    only = await store.async_query_events(device_id="d1", access_only=True)
    assert [r["event_uid"] for r in only] == ["d1:1"]


async def test_query_filters_and_cursor(store):
    base = datetime(2026, 9, 1, tzinfo=UTC)
    for i in range(5):
        await store.async_insert_event(
            _event(
                f"d1:{i}", str(i), base + timedelta(minutes=i),
                access_result="granted" if i % 2 else "denied",
                person_id="7" if i == 3 else None,
            )
        )
    granted = await store.async_query_events(device_id="d1", result="granted")
    assert {r["event_uid"] for r in granted} == {"d1:1", "d1:3"}

    person = await store.async_query_events(person_id="7")
    assert len(person) == 1 and person[0]["event_uid"] == "d1:3"

    page1 = await store.async_query_events(device_id="d1", limit=2)
    assert len(page1) == 2
    cur = (page1[-1]["timestamp"], page1[-1]["event_uid"])
    page2 = await store.async_query_events(device_id="d1", limit=2, before=cur)
    assert page2[0]["event_uid"] != page1[0]["event_uid"]


async def test_update_fields_and_person_cache(store):
    ts = datetime(2026, 9, 1, tzinfo=UTC)
    await store.async_insert_event(_event("d1:1", "1", ts, person_id="25"))
    await store.async_update_event_fields("d1:1", person_name="Fulano",
                                          event_picture_path="/x/y.jpg")
    row = await store.async_get_event("d1:1")
    assert row["person_name"] == "Fulano"
    assert row["event_picture_path"] == "/x/y.jpg"

    await store.async_upsert_person("dev-serial", "25", "Fulano", "/p/25.jpg")
    p = await store.async_get_person("dev-serial", "25")
    assert p["person_name"] == "Fulano" and p["picture_path"] == "/p/25.jpg"
    # COALESCE keeps existing name when a later upsert passes None
    await store.async_upsert_person("dev-serial", "25", None, None)
    p2 = await store.async_get_person("dev-serial", "25")
    assert p2["person_name"] == "Fulano"


async def test_retention_purge_returns_paths(store):
    old = datetime.now(UTC) - timedelta(days=200)
    new = datetime.now(UTC)
    await store.async_insert_event(_event("d1:1", "1", old, event_picture_path="/old.jpg"))
    await store.async_insert_event(_event("d1:2", "2", new, event_picture_path="/new.jpg"))
    cutoff = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    paths = await store.async_purge_images_before(cutoff)
    assert paths == ["/old.jpg"]
    assert (await store.async_get_event("d1:1"))["event_picture_path"] is None
    assert (await store.async_get_event("d1:2"))["event_picture_path"] == "/new.jpg"


def test_compute_event_uid_prefers_native_serial():
    ts = datetime(2026, 9, 1, tzinfo=UTC)
    assert compute_event_uid("SER", native_serial=99, major=5, minor=75,
                              timestamp=ts, person_id="1") == "SER:99"
    h = compute_event_uid("SER", native_serial=None, major=5, minor=75,
                          timestamp=ts, person_id="1")
    assert h.startswith("h:") and len(h) == 34
