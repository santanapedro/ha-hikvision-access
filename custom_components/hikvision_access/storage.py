"""Per-terminal SQLite store for access events (spec §8, §13, §55).

Kept deliberately separate from Home Assistant's recorder DB. One file per
config entry under ``<config>/hikvision_access/<entry_id>/``.

All SQLite work runs in the executor; the public API is async. The connection
is opened with ``check_same_thread=False`` and guarded by an asyncio lock so
only one executor job touches it at a time.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .models import AccessEvent

_LOGGER = logging.getLogger(__name__)

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS devices (
    id TEXT PRIMARY KEY,
    name TEXT,
    model TEXT,
    serial_number TEXT,
    firmware TEXT,
    mac TEXT,
    last_seen TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_uid TEXT PRIMARY KEY,
    device_id TEXT NOT NULL,
    timestamp TEXT NOT NULL,
    serial_number TEXT,
    door_id TEXT,
    door_name TEXT,
    person_id TEXT,
    person_name TEXT,
    card_number TEXT,
    authentication_method TEXT,
    access_result TEXT,
    major_event_type TEXT,
    minor_event_type TEXT,
    event_picture_url TEXT,
    event_picture_path TEXT,
    user_picture_path TEXT,
    is_live INTEGER,
    raw_payload TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_person ON events(person_id);
CREATE INDEX IF NOT EXISTS idx_events_device ON events(device_id);
CREATE INDEX IF NOT EXISTS idx_events_result ON events(access_result);
CREATE INDEX IF NOT EXISTS idx_events_serial ON events(device_id, serial_number);

CREATE TABLE IF NOT EXISTS persons_cache (
    device_id TEXT NOT NULL,
    person_id TEXT NOT NULL,
    person_name TEXT,
    picture_path TEXT,
    updated_at TEXT,
    PRIMARY KEY (device_id, person_id)
);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class EventStore:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._conn: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    # ---- lifecycle ----------------------------------------------------

    async def async_open(self) -> None:
        await asyncio.get_running_loop().run_in_executor(None, self._open)

    def _open(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            self._path, check_same_thread=False, isolation_level=None
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        row = self._conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,)
            )
        else:
            self._migrate(int(row["version"]))

    def _migrate(self, current: int) -> None:
        # future: apply ordered migrations current+1..SCHEMA_VERSION (spec §55)
        if current > SCHEMA_VERSION:
            _LOGGER.warning(
                "hikvision_access DB schema %s is newer than this release (%s)",
                current,
                SCHEMA_VERSION,
            )

    async def async_close(self) -> None:
        async with self._lock:
            if self._conn is not None:
                await asyncio.get_running_loop().run_in_executor(
                    None, self._conn.close
                )
                self._conn = None

    # ---- writes -----------------------------------------------------

    async def async_upsert_device(self, info: dict[str, Any]) -> None:
        await self._run(self._upsert_device, info)

    def _upsert_device(self, info: dict[str, Any]) -> None:
        assert self._conn
        now = _now()
        self._conn.execute(
            """
            INSERT INTO devices (id, name, model, serial_number, firmware, mac,
                                 last_seen, created_at)
            VALUES (:id, :name, :model, :serial_number, :firmware, :mac, :last_seen,
                    :created_at)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, model=excluded.model, firmware=excluded.firmware,
                mac=excluded.mac, last_seen=excluded.last_seen
            """,
            {"created_at": now, "last_seen": now, **info},
        )

    async def async_insert_event(self, event: AccessEvent) -> bool:
        """Insert one event. Returns True if it was new, False if a duplicate."""
        return await self._run(self._insert_event, event)

    def _insert_event(self, event: AccessEvent) -> bool:
        assert self._conn
        payload = event.raw_payload
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload, default=str, ensure_ascii=False)
        try:
            self._conn.execute(
                """
                INSERT INTO events (
                    event_uid, device_id, timestamp, serial_number, door_id,
                    door_name, person_id, person_name, card_number,
                    authentication_method, access_result, major_event_type,
                    minor_event_type, event_picture_url, event_picture_path,
                    user_picture_path, is_live, raw_payload, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    event.event_uid,
                    event.device_id,
                    _iso(event.timestamp),
                    event.serial_number,
                    event.door_id,
                    event.door_name,
                    event.person_id,
                    event.person_name,
                    event.card_number,
                    event.authentication_method,
                    event.access_result,
                    _s(event.major_event_type),
                    _s(event.minor_event_type),
                    event.event_picture_url,
                    event.event_picture_path,
                    event.user_picture_path,
                    None if event.is_live is None else int(event.is_live),
                    payload,
                    _now(),
                ),
            )
            return True
        except sqlite3.IntegrityError:
            return False

    async def async_update_event_fields(self, event_uid: str, **fields: Any) -> None:
        if not fields:
            return
        await self._run(self._update_event_fields, event_uid, fields)

    def _update_event_fields(self, event_uid: str, fields: dict[str, Any]) -> None:
        assert self._conn
        cols = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(
            f"UPDATE events SET {cols} WHERE event_uid=?",
            (*fields.values(), event_uid),
        )

    async def async_set_meta(self, key: str, value: str) -> None:
        await self._run(
            lambda: self._conn.execute(  # type: ignore[union-attr]
                "INSERT INTO meta (key, value) VALUES (?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        )

    async def async_get_meta(self, key: str) -> str | None:
        def _get() -> str | None:
            assert self._conn
            row = self._conn.execute(
                "SELECT value FROM meta WHERE key=?", (key,)
            ).fetchone()
            return row["value"] if row else None

        return await self._run(_get)

    async def async_upsert_person(
        self, device_id: str, person_id: str, name: str | None, picture_path: str | None
    ) -> None:
        await self._run(
            lambda: self._conn.execute(  # type: ignore[union-attr]
                """
                INSERT INTO persons_cache (device_id, person_id, person_name,
                                           picture_path, updated_at)
                VALUES (?,?,?,?,?)
                ON CONFLICT(device_id, person_id) DO UPDATE SET
                    person_name=COALESCE(excluded.person_name, persons_cache.person_name),
                    picture_path=COALESCE(excluded.picture_path, persons_cache.picture_path),
                    updated_at=excluded.updated_at
                """,
                (device_id, person_id, name, picture_path, _now()),
            )
        )

    # ---- reads ------------------------------------------------------

    async def async_get_person(
        self, device_id: str, person_id: str
    ) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            assert self._conn
            row = self._conn.execute(
                "SELECT * FROM persons_cache WHERE device_id=? AND person_id=?",
                (device_id, person_id),
            ).fetchone()
            return dict(row) if row else None

        return await self._run(_get)

    async def async_max_serial(self, device_id: str) -> int | None:
        def _get() -> int | None:
            assert self._conn
            row = self._conn.execute(
                "SELECT MAX(CAST(serial_number AS INTEGER)) AS m FROM events "
                "WHERE device_id=? AND serial_number IS NOT NULL",
                (device_id,),
            ).fetchone()
            return int(row["m"]) if row and row["m"] is not None else None

        return await self._run(_get)

    async def async_get_event(self, event_uid: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            assert self._conn
            row = self._conn.execute(
                "SELECT * FROM events WHERE event_uid=?", (event_uid,)
            ).fetchone()
            return dict(row) if row else None

        return await self._run(_get)

    async def async_latest_event(self, device_id: str) -> dict[str, Any] | None:
        def _get() -> dict[str, Any] | None:
            assert self._conn
            row = self._conn.execute(
                "SELECT * FROM events WHERE device_id=? "
                "ORDER BY timestamp DESC, created_at DESC LIMIT 1",
                (device_id,),
            ).fetchone()
            return dict(row) if row else None

        return await self._run(_get)

    async def async_query_events(
        self,
        *,
        device_id: str | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        result: str | None = None,
        person_id: str | None = None,
        limit: int = 50,
        before: tuple[str, str] | None = None,  # (timestamp, event_uid) cursor
        access_only: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if access_only:
            # exclude pure door relay/contact events (no person, unknown result)
            clauses.append(
                "(person_id IS NOT NULL OR access_result IN ('granted','denied'))"
            )
        if device_id:
            clauses.append("device_id=?")
            params.append(device_id)
        if start:
            clauses.append("timestamp>=?")
            params.append(_iso(start))
        if end:
            clauses.append("timestamp<=?")
            params.append(_iso(end))
        if result:
            clauses.append("access_result=?")
            params.append(result)
        if person_id:
            clauses.append("person_id=?")
            params.append(person_id)
        if before:
            clauses.append("(timestamp, event_uid) < (?, ?)")
            params.extend(before)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM events {where} "
            f"ORDER BY timestamp DESC, event_uid DESC LIMIT ?"
        )
        params.append(min(limit, 500))

        def _q() -> list[dict[str, Any]]:
            assert self._conn
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

        return await self._run(_q)

    async def async_picture_stats(self, device_id: str) -> dict[str, int]:
        def _q() -> dict[str, int]:
            assert self._conn
            row = self._conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(access_result IN ('granted','denied')) AS decisions, "
                "SUM(event_picture_url IS NOT NULL) AS with_url, "
                "SUM(event_picture_path IS NOT NULL) AS with_file "
                "FROM events WHERE device_id=?",
                (device_id,),
            ).fetchone()
            return {
                k: int(row[k] or 0)
                for k in ("total", "decisions", "with_url", "with_file")
            }

        return await self._run(_q)

    async def async_recent_decisions(
        self, device_id: str, limit: int = 15
    ) -> list[dict[str, Any]]:
        def _q() -> list[dict[str, Any]]:
            assert self._conn
            rows = self._conn.execute(
                "SELECT serial_number, timestamp, person_id, minor_event_type, "
                "is_live, (event_picture_url IS NOT NULL) AS has_url, "
                "(event_picture_path IS NOT NULL) AS has_file "
                "FROM events WHERE device_id=? "
                "AND access_result IN ('granted','denied') "
                "ORDER BY timestamp DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(_q)

    async def async_events_missing_pictures(
        self, device_id: str, limit: int = 20
    ) -> list[dict[str, Any]]:
        """Newest events that have a pictureURL but no downloaded file yet."""

        def _q() -> list[dict[str, Any]]:
            assert self._conn
            rows = self._conn.execute(
                "SELECT event_uid, event_picture_url, timestamp FROM events "
                "WHERE device_id=? AND event_picture_url IS NOT NULL "
                "AND event_picture_path IS NULL "
                "ORDER BY timestamp DESC LIMIT ?",
                (device_id, limit),
            ).fetchall()
            return [dict(r) for r in rows]

        return await self._run(_q)

    async def async_count_today(self, device_id: str, day_start_iso: str) -> int:
        def _c() -> int:
            assert self._conn
            row = self._conn.execute(
                "SELECT COUNT(*) AS n FROM events WHERE device_id=? AND timestamp>=?",
                (device_id, day_start_iso),
            ).fetchone()
            return int(row["n"])

        return await self._run(_c)

    async def async_purge_images_before(self, cutoff_iso: str) -> list[str]:
        """Null out picture paths older than cutoff; return the paths to unlink."""

        def _purge() -> list[str]:
            assert self._conn
            rows = self._conn.execute(
                "SELECT event_uid, event_picture_path FROM events "
                "WHERE event_picture_path IS NOT NULL AND timestamp < ?",
                (cutoff_iso,),
            ).fetchall()
            paths = [r["event_picture_path"] for r in rows]
            if paths:
                self._conn.execute(
                    "UPDATE events SET event_picture_path=NULL WHERE timestamp < ?",
                    (cutoff_iso,),
                )
            return paths

        return await self._run(_purge)

    # ---- internals ------------------------------------------------

    async def _run(self, fn, *args):
        async with self._lock:
            return await asyncio.get_running_loop().run_in_executor(
                None, fn, *args
            )


def compute_event_uid(
    device_serial: str,
    *,
    native_serial: str | int | None,
    major: Any,
    minor: Any,
    timestamp: datetime | str,
    person_id: str | None,
) -> str:
    """Stable id for dedupe across stream + reconciliation (spec §8)."""
    if native_serial not in (None, "", 0, "0"):
        return f"{device_serial}:{native_serial}"
    import hashlib

    ts = timestamp if isinstance(timestamp, str) else _iso(timestamp)
    raw = f"{device_serial}|{ts}|{person_id or ''}|{major}|{minor}"
    return "h:" + hashlib.sha256(raw.encode()).hexdigest()[:32]


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _iso(value: datetime | str) -> str:
    return value if isinstance(value, str) else value.isoformat()


def _s(value: Any) -> str | None:
    return None if value is None else str(value)
