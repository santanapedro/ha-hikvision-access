"""Diagnostics export (spec §32). Redacts host, credentials, serial, names."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import HikvisionAccessEntry
from .const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

TO_REDACT = {CONF_HOST, CONF_USERNAME, CONF_PASSWORD, "serial_number", "mac", "unique_id"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: HikvisionAccessEntry
) -> dict[str, Any]:
    rt = entry.runtime_data
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
            "electro_lock_num": rt.info.electro_lock_num,
        },
        "capabilities": {
            k: v
            for k, v in vars(rt.capabilities).items()
            if k != "raw"
        },
        "health": {
            "last_update_success": rt.health.last_update_success,
            "data": vars(rt.health.data) if rt.health.data else None,
        },
    }
