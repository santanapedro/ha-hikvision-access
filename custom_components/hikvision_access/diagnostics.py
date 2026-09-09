"""Diagnostics export (spec §32). Redacts host, credentials, serial, names."""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HikvisionAccessEntry
from .const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "serial_number", "mac", "unique_id"}


def _asdict(obj: Any) -> Any:
    return asdict(obj) if is_dataclass(obj) else obj


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikvisionAccessEntry
) -> dict[str, Any]:
    rt = entry.runtime_data
    caps = _asdict(rt.capabilities)
    caps.pop("raw", None)
    last = rt.gateway.last_access_event

    stats = await rt.store.async_picture_stats(rt.info.serial_number)
    recent = await rt.store.async_recent_decisions(rt.info.serial_number, 15)

    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "device": {
            "model": rt.info.model,
            "firmware": rt.info.firmware,
            "device_type": rt.info.device_type,
            "sub_device_type": rt.info.sub_device_type,
        },
        "capabilities": caps,
        "health": {
            "last_update_success": rt.health.last_update_success,
            "data": _asdict(rt.health.data) if rt.health.data else None,
        },
        "call": {"enabled": rt.call is not None, "status": rt.call.data if rt.call else None},
        "listener": rt.gateway.health_snapshot(),
        "pictures": {**stats, "lock_remaining_s": rt.client.lock_remaining},
        "recent_decisions": [
            {
                "serial": r.get("serial_number"),
                "ts": r.get("timestamp"),
                "person": bool(r.get("person_id")),
                "minor": r.get("minor_event_type"),
                "live": r.get("is_live"),
                "has_url": r.get("has_url"),
                "has_file": r.get("has_file"),
            }
            for r in recent
        ],
        "last_access": (
            {
                "person": last.person_name,
                "result": last.access_result,
                "picture_url": last.event_picture_url,
                "picture_path": last.event_picture_path,
            }
            if last
            else None
        ),
    }
