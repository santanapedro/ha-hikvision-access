"""Integration services (spec §17)."""

from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr

from .const import DATA_PUSH_SLOT, DOMAIN
from .exceptions import HikvisionError

_LOGGER = logging.getLogger(__name__)

SERVICE_OPEN_DOOR = "open_door"
SERVICE_RECONCILE_NOW = "reconcile_now"
SERVICE_SYNC_PERSONS = "sync_persons"
SERVICE_SETUP_PUSH = "setup_push"
SERVICE_REMOVE_PUSH = "remove_push"

_TARGET = {
    vol.Exclusive("entry_id", "target"): str,
    vol.Exclusive("device_id", "target"): str,
}

_OPEN_DOOR_SCHEMA = vol.Schema(
    {**_TARGET, vol.Optional("door", default=1): vol.All(int, vol.Range(min=1, max=8))}
)
_TARGET_SCHEMA = vol.Schema(_TARGET)


def _resolve_entry(hass: HomeAssistant, call: ServiceCall):
    entry_id = call.data.get("entry_id")
    if not entry_id and call.data.get("device_id"):
        device = dr.async_get(hass).async_get(call.data["device_id"])
        if device:
            entry_id = next(
                (e for e in device.config_entries
                 if hass.config_entries.async_get_entry(e)
                 and hass.config_entries.async_get_entry(e).domain == DOMAIN),
                None,
            )
    if not entry_id:
        entries = hass.config_entries.async_entries(DOMAIN)
        if len(entries) == 1:
            entry_id = entries[0].entry_id
    entry = hass.config_entries.async_get_entry(entry_id) if entry_id else None
    if entry is None or getattr(entry, "runtime_data", None) is None:
        raise ServiceValidationError(
            "Terminal Hikvision Access não encontrado para o alvo informado."
        )
    return entry


async def async_setup_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_OPEN_DOOR):
        return

    async def _open_door(call: ServiceCall) -> None:
        rt = _resolve_entry(hass, call).runtime_data
        if not rt.capabilities.remote_door_control:
            raise HomeAssistantError("Este terminal não suporta abertura remota.")
        door = call.data["door"]
        _LOGGER.info("service open_door: %s porta %s", rt.info.serial_number, door)
        try:
            await rt.client.async_remote_door(door, "open")
        except HikvisionError as err:
            raise HomeAssistantError(f"Falha ao abrir a porta: {err}") from err

    async def _reconcile_now(call: ServiceCall) -> None:
        rt = _resolve_entry(hass, call).runtime_data
        count = await rt.reconciler.async_run_once()
        _LOGGER.info("service reconcile_now: %d novo(s) evento(s)", count)

    async def _sync_persons(call: ServiceCall) -> None:
        rt = _resolve_entry(hass, call).runtime_data
        loaded = await rt.persons.async_prime()
        _LOGGER.info("service sync_persons: %d usuário(s) em cache", loaded)

    async def _setup_push(call: ServiceCall) -> None:
        from .push import async_claim_slot

        entry = _resolve_entry(hass, call)
        rt = entry.runtime_data
        token = entry.data.get("push_token")
        if not token:
            raise HomeAssistantError("entry sem push_token; recarregue a integração.")
        try:
            slot = await async_claim_slot(hass, rt.client, token)
        except HikvisionError as err:
            raise HomeAssistantError(f"Falha ao registrar push: {err}") from err
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, DATA_PUSH_SLOT: slot}
        )
        _LOGGER.info("service setup_push: slot %s registrado", slot)

    async def _remove_push(call: ServiceCall) -> None:
        from .push import async_release_slot

        entry = _resolve_entry(hass, call)
        rt = entry.runtime_data
        slot = entry.data.get(DATA_PUSH_SLOT)
        if not slot:
            return
        try:
            await async_release_slot(rt.client, int(slot))
        except HikvisionError as err:
            raise HomeAssistantError(f"Falha ao remover push: {err}") from err
        hass.config_entries.async_update_entry(
            entry, data={k: v for k, v in entry.data.items() if k != DATA_PUSH_SLOT}
        )

    hass.services.async_register(DOMAIN, SERVICE_OPEN_DOOR, _open_door, _OPEN_DOOR_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_RECONCILE_NOW, _reconcile_now, _TARGET_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, SERVICE_SYNC_PERSONS, _sync_persons, _TARGET_SCHEMA
    )
    hass.services.async_register(DOMAIN, SERVICE_SETUP_PUSH, _setup_push, _TARGET_SCHEMA)
    hass.services.async_register(
        DOMAIN, SERVICE_REMOVE_PUSH, _remove_push, _TARGET_SCHEMA
    )
