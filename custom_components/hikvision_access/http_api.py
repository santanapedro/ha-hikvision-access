"""Authenticated frontend API (spec §24, §25).

    GET  /api/hikvision_access/events?entry_id=&start=&end=&result=&person_id=&limit=
    GET  /api/hikvision_access/events/{event_uid}
    GET  /api/hikvision_access/events/{event_uid}/image
    GET  /api/hikvision_access/persons/{entry_id}/{person_id}/image

Plus a WebSocket subscription ``hikvision_access/subscribe`` that pushes each
new access event so the (future) Lovelace card does not have to poll.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import voluptuous as vol
from aiohttp import web
from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .gateway import signal_access

_LOGGER = logging.getLogger(__name__)

_REGISTERED = f"{DOMAIN}_http_api_registered"


def async_register(hass: HomeAssistant) -> None:
    """Register views + WS command once for the whole integration."""
    if hass.data.get(_REGISTERED):
        return
    hass.data[_REGISTERED] = True
    hass.http.register_view(EventsView)
    hass.http.register_view(EventDetailView)
    hass.http.register_view(EventImageView)
    hass.http.register_view(PersonImageView)
    websocket_api.async_register_command(hass, ws_subscribe)


def _runtimes(hass: HomeAssistant) -> dict[str, Any]:
    out = {}
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.state is ConfigEntryState.LOADED and getattr(
            entry, "runtime_data", None
        ):
            out[entry.entry_id] = entry.runtime_data
    return out


def _resolve_store(hass: HomeAssistant, entry_id: str | None):
    rts = _runtimes(hass)
    if entry_id:
        rt = rts.get(entry_id)
        return (rt.store, rt) if rt else (None, None)
    if len(rts) == 1:
        rt = next(iter(rts.values()))
        return rt.store, rt
    return None, None


class EventsView(HomeAssistantView):
    url = "/api/hikvision_access/events"
    name = "api:hikvision_access:events"

    async def get(self, request: web.Request) -> web.Response:
        hass = request.app["hass"]
        q = request.query

        def _dt(name: str):
            raw = q.get(name)
            return dt_util.parse_datetime(raw) if raw else None

        try:
            limit = min(int(q.get("limit", "50")), 200)
        except ValueError:
            limit = 50
        before = None
        if q.get("cursor"):
            ts, _, uid = q["cursor"].partition("|")
            before = (ts, uid)

        rts = _runtimes(hass)
        wanted = q.get("entry_id")
        targets = {wanted: rts[wanted]} if wanted in rts else rts
        if not targets:
            return self.json({"events": [], "next_cursor": None})

        names = {
            rt.info.serial_number: rt.gateway.device_name for rt in targets.values()
        }
        access_only = q.get("all", "").lower() not in ("1", "true", "yes")
        merged: list[dict] = []
        for rt in targets.values():
            merged += await rt.store.async_query_events(
                start=_dt("start"),
                end=_dt("end"),
                result=q.get("result"),
                person_id=q.get("person_id"),
                limit=limit,
                before=before,
                access_only=access_only,
            )
        merged.sort(key=lambda r: (r["timestamp"], r["event_uid"]), reverse=True)
        merged = merged[:limit]

        next_cursor = None
        if len(merged) == limit:
            last = merged[-1]
            next_cursor = f"{last['timestamp']}|{last['event_uid']}"
        return self.json(
            {
                "events": [_public(r, names) for r in merged],
                "next_cursor": next_cursor,
            }
        )


class EventDetailView(HomeAssistantView):
    url = "/api/hikvision_access/events/{event_uid}"
    name = "api:hikvision_access:event-detail"

    async def get(self, request: web.Request, event_uid: str) -> web.Response:
        hass = request.app["hass"]
        for rt in _runtimes(hass).values():
            row = await rt.store.async_get_event(event_uid)
            if row:
                return self.json(_public(row))
        return self.json_message("not found", 404)


class EventImageView(HomeAssistantView):
    url = "/api/hikvision_access/events/{event_uid}/image"
    name = "api:hikvision_access:event-image"

    async def get(
        self, request: web.Request, event_uid: str
    ) -> web.StreamResponse:
        hass = request.app["hass"]
        for rt in _runtimes(hass).values():
            row = await rt.store.async_get_event(event_uid)
            if row and row.get("event_picture_path"):
                return await _serve_image(hass, row["event_picture_path"])
        return web.Response(status=404)


class PersonImageView(HomeAssistantView):
    url = "/api/hikvision_access/persons/{entry_id}/{person_id}/image"
    name = "api:hikvision_access:person-image"

    async def get(
        self, request: web.Request, entry_id: str, person_id: str
    ) -> web.StreamResponse:
        hass = request.app["hass"]
        store, _ = _resolve_store(hass, entry_id)
        if store is None:
            return web.Response(status=404)
        for rt in _runtimes(hass).values():
            person = await store.async_get_person(rt.info.serial_number, person_id)
            if person and person.get("picture_path"):
                return await _serve_image(hass, person["picture_path"])
        return web.Response(status=404)


async def _serve_image(hass: HomeAssistant, path: str) -> web.StreamResponse:
    p = Path(path)
    if not await hass.async_add_executor_job(p.is_file):
        return web.Response(status=404)
    # FileResponse streams from disk and adds Last-Modified / ETag / Range,
    # so repeat views (lightbox, scroll-back) are cheap and don't buffer the
    # whole JPEG in memory.
    return web.FileResponse(
        p,
        headers={
            "Content-Type": "image/jpeg",
            "Cache-Control": "private, max-age=86400",
        },
    )


def _public(
    row: dict[str, Any], names: dict[str, str] | None = None
) -> dict[str, Any]:
    return {
        "event_uid": row["event_uid"],
        "timestamp": row["timestamp"],
        "person_id": row.get("person_id"),
        "person_name": row.get("person_name"),
        "device_id": row.get("device_id"),
        "device_name": (names or {}).get(row.get("device_id", "")),
        "door_name": row.get("door_name") or row.get("door_id"),
        "method": row.get("authentication_method"),
        "result": row.get("access_result"),
        "major": row.get("major_event_type"),
        "minor": row.get("minor_event_type"),
        "has_event_picture": bool(row.get("event_picture_path")),
        "has_user_picture": bool(row.get("user_picture_path")),
    }


@websocket_api.websocket_command(
    {
        vol.Required("type"): "hikvision_access/subscribe",
        vol.Optional("entry_id"): str,
    }
)
@callback
def ws_subscribe(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    entry_id = msg.get("entry_id")
    targets = [entry_id] if entry_id else list(_runtimes(hass))
    unsubs = []

    @callback
    def _forward(event) -> None:
        connection.send_message(
            {
                "id": msg["id"],
                "type": "event",
                "event": {
                    "event_uid": event.event_uid,
                    "timestamp": event.timestamp.isoformat(),
                    "person_name": event.person_name,
                    "person_id": event.person_id,
                    "result": event.access_result,
                    "method": event.authentication_method,
                    "has_picture": bool(event.event_picture_path),
                },
            }
        )

    for tid in targets:
        unsubs.append(async_dispatcher_connect(hass, signal_access(tid), _forward))

    @callback
    def _stop() -> None:
        for u in unsubs:
            u()

    connection.subscriptions[msg["id"]] = _stop
    connection.send_result(msg["id"])
