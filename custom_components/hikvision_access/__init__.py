"""The Hikvision Access integration.

Each config entry is one physical terminal, keyed by its serial number, with
its own ISAPI client, SQLite store, event listener/reconciler and entities.
Entries are independent — one terminal offline never affects another.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_track_time_interval

from .api import HikvisionISAPIClient
from .capabilities import async_discover
from .const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USE_HTTPS,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
    DATA_PUSH_SLOT,
    DATA_PUSH_TOKEN,
    DEFAULT_EVENT_ROUTE,
    DEFAULT_IMAGE_RETENTION_DAYS,
    DEFAULT_RECONCILE_INTERVAL_S,
    DOMAIN,
    EP_ALERT_STREAM,
    EP_DOOR_PARAM,
    OPT_ALSO_RUN_STREAM,
    OPT_EVENT_ROUTE,
    OPT_IMAGE_RETENTION_DAYS,
    OPT_RECONCILE_INTERVAL,
    OPT_REGISTER_PUSH_ON_DEVICE,
)
from .coordinator import HikvisionCallCoordinator, HikvisionHealthCoordinator
from .event_listener import EventListener
from .event_reconciler import EventReconciler
from .exceptions import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionError,
    HikvisionLockoutError,
)
from .gateway import EventGateway
from .http_api import async_register as async_register_http_api
from .image_manager import ImageManager
from .models import DeviceCapabilities, DeviceInfo
from .person_manager import PersonManager
from .push import HikvisionPushView, async_claim_slot, async_release_slot, new_token
from .services import async_setup_services
from .storage import EventStore

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CAMERA,
    Platform.EVENT,
    Platform.IMAGE,
    Platform.SENSOR,
]

_PUSH_VIEW = "push_view"


@dataclass
class HikvisionAccessRuntime:
    client: HikvisionISAPIClient
    info: DeviceInfo
    capabilities: DeviceCapabilities
    health: HikvisionHealthCoordinator
    store: EventStore
    images: ImageManager
    persons: PersonManager
    gateway: EventGateway
    listener: EventListener | None
    reconciler: EventReconciler
    door_name: str | None
    call: HikvisionCallCoordinator | None = None
    unsubs: list = field(default_factory=list)


type HikvisionAccessEntry = ConfigEntry[HikvisionAccessRuntime]


def _get_push_view(hass: HomeAssistant) -> HikvisionPushView:
    store = hass.data.setdefault(DOMAIN, {})
    view = store.get(_PUSH_VIEW)
    if view is None:
        view = HikvisionPushView()
        hass.http.register_view(view)
        store[_PUSH_VIEW] = view
    return view


_CARD_URL = "/hikvision_access_frontend/hikvision-access-card.js"
_CARD_VERSION = "0.2.7"


async def _async_register_frontend(hass: HomeAssistant) -> None:
    """Serve the Lovelace card and make it available without a manual resource.

    Best-effort: a missing frontend (e.g. in a headless test env) must not
    fail integration setup.
    """
    store = hass.data.setdefault(DOMAIN, {})
    if store.get("frontend"):
        return
    store["frontend"] = True
    try:
        from homeassistant.components.http import StaticPathConfig

        src = Path(__file__).parent / "frontend" / "hikvision-access-card.js"
        await hass.http.async_register_static_paths(
            [StaticPathConfig(_CARD_URL, str(src), cache_headers=False)]
        )
        if "frontend" in hass.config.components:
            from homeassistant.components.frontend import add_extra_js_url

            add_extra_js_url(hass, f"{_CARD_URL}?v={_CARD_VERSION}")
        else:
            _LOGGER.debug("frontend not loaded; card served but not auto-added")
    except Exception:
        _LOGGER.warning("could not register the Lovelace card", exc_info=True)


async def async_setup_entry(hass: HomeAssistant, entry: HikvisionAccessEntry) -> bool:
    data = entry.data
    opts = entry.options
    session = async_get_clientsession(hass, verify_ssl=data[CONF_VERIFY_SSL])
    client = HikvisionISAPIClient(
        session,
        data[CONF_HOST],
        data[CONF_PORT],
        data[CONF_USERNAME],
        data[CONF_PASSWORD],
        use_https=data[CONF_USE_HTTPS],
    )

    try:
        info = await client.async_get_device_info()
        capabilities = await async_discover(client)
        door_name = await _read_door_name(client)
    except HikvisionLockoutError as err:
        raise ConfigEntryNotReady(f"Terminal bloqueou logins: {err}") from err
    except HikvisionAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except (HikvisionConnectionError, HikvisionError) as err:
        raise ConfigEntryNotReady(
            f"Não foi possível conectar ao terminal em {data[CONF_HOST]}: {err}"
        ) from err

    base_dir = Path(hass.config.path("hikvision_access", entry.entry_id))
    store = EventStore(base_dir / "hikvision_access.db")
    await store.async_open()
    await store.async_upsert_device(
        {
            "id": info.serial_number,
            "name": entry.title,
            "model": info.model,
            "serial_number": info.serial_number,
            "firmware": info.firmware,
            "mac": info.mac,
        }
    )

    images = ImageManager(
        client,
        base_dir / "media",
        opts.get(OPT_IMAGE_RETENTION_DAYS, DEFAULT_IMAGE_RETENTION_DAYS),
    )
    await images.async_setup()
    persons = PersonManager(client, store, info.serial_number, images)

    gateway = EventGateway(
        hass,
        entry.entry_id,
        info.serial_number,
        entry.title,
        store,
        images,
        persons,
        dict(opts),
    )
    await gateway.async_restore()

    health = HikvisionHealthCoordinator(hass, entry, client)
    await health.async_config_entry_first_refresh()

    call: HikvisionCallCoordinator | None = None
    if capabilities.intercom:
        call = HikvisionCallCoordinator(hass, entry, client)
        try:
            await call.async_config_entry_first_refresh()
        except ConfigEntryNotReady:
            call = None  # keep the rest of the integration working

    reconciler = EventReconciler(
        hass,
        client,
        store,
        gateway,
        info.serial_number,
        info.serial_number,
        int(opts.get(OPT_RECONCILE_INTERVAL, DEFAULT_RECONCILE_INTERVAL_S)),
        door_name=door_name,
    )

    route = opts.get(OPT_EVENT_ROUTE, DEFAULT_EVENT_ROUTE)
    push_active = bool(entry.data.get(DATA_PUSH_SLOT))
    # Always keep a realtime path: the stream listener runs unless push is both
    # selected AND actually registered on the terminal.
    run_stream = (
        route == "stream"
        or opts.get(OPT_ALSO_RUN_STREAM, False)
        or (route == "push" and not push_active)
    )
    listener: EventListener | None = None
    if run_stream and capabilities.event_stream:
        listener = EventListener(
            hass,
            client,
            gateway,
            info.serial_number,
            info.serial_number,
            EP_ALERT_STREAM,
            door_name=door_name,
            on_reconnect=reconciler.async_run_once,
        )

    unsubs: list = []
    runtime = HikvisionAccessRuntime(
        client=client,
        info=info,
        capabilities=capabilities,
        health=health,
        store=store,
        images=images,
        persons=persons,
        gateway=gateway,
        listener=listener,
        reconciler=reconciler,
        door_name=door_name,
        call=call,
        unsubs=unsubs,
    )
    entry.runtime_data = runtime

    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, info.serial_number)},
        manufacturer="Hikvision",
        model=info.model,
        name=entry.title,
        sw_version=info.firmware,
        serial_number=info.serial_number,
    )

    # frontend API + Lovelace card (once) + push view target
    async_register_http_api(hass)
    await _async_register_frontend(hass)
    push_view = _get_push_view(hass)
    token = entry.data.get(DATA_PUSH_TOKEN) or new_token()
    if token != entry.data.get(DATA_PUSH_TOKEN):
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, DATA_PUSH_TOKEN: token}
        )
    push_view.register_target(
        token, gateway, info.serial_number, info.serial_number, door_name
    )

    if route == "push" and opts.get(OPT_REGISTER_PUSH_ON_DEVICE, False):
        try:
            slot = await async_claim_slot(hass, client, token)
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, DATA_PUSH_SLOT: slot}
            )
        except (HikvisionError, Exception) as err:  # noqa: BLE001
            _LOGGER.warning(
                "não foi possível registrar o push no terminal %s: %s — "
                "usando apenas reconciliação até você habilitar/liberar um slot",
                info.serial_number,
                err,
            )
    elif route == "push":
        _LOGGER.info(
            "rota 'push' selecionada mas 'registrar push no terminal' está "
            "desligado. Habilite nas opções ou aponte um slot httpHosts para "
            "%s manualmente.",
            token,
        )

    await async_setup_services(hass)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # start realtime + periodic work only after entities exist
    await reconciler.async_start()
    if listener is not None:
        listener.start()

    @callback
    def _schedule_purge(_now) -> None:
        hass.async_create_task(_daily_purge(runtime))

    unsubs.append(
        async_track_time_interval(hass, _schedule_purge, timedelta(hours=6))
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_update))
    return True


async def _read_door_name(client: HikvisionISAPIClient) -> str | None:
    try:
        data = await client.async_get_caps(EP_DOOR_PARAM.format(door=1))
    except HikvisionError:
        return None
    node = data.get("DoorParam", data)
    return node.get("doorName") or None


async def _daily_purge(runtime: HikvisionAccessRuntime) -> None:
    try:
        removed = await runtime.images.async_purge(runtime.store)
        if removed:
            _LOGGER.info("purged %d expired image(s)", removed)
    except Exception:
        _LOGGER.exception("image purge failed")


async def _async_reload_on_update(
    hass: HomeAssistant, entry: HikvisionAccessEntry
) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HikvisionAccessEntry) -> bool:
    runtime = entry.runtime_data
    if runtime.listener is not None:
        await runtime.listener.async_stop()
    runtime.reconciler.async_stop()
    for unsub in runtime.unsubs:
        unsub()

    token = entry.data.get(DATA_PUSH_TOKEN)
    view: HikvisionPushView | None = hass.data.get(DOMAIN, {}).get(_PUSH_VIEW)
    if view and token:
        view.unregister_target(token)

    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        await runtime.store.async_close()
    return unloaded


async def async_remove_entry(hass: HomeAssistant, entry: HikvisionAccessEntry) -> None:
    """Release the terminal's push slot when the integration is removed."""
    slot = entry.data.get(DATA_PUSH_SLOT)
    if not slot:
        return
    try:
        session = async_get_clientsession(hass, verify_ssl=entry.data[CONF_VERIFY_SSL])
        client = HikvisionISAPIClient(
            session,
            entry.data[CONF_HOST],
            entry.data[CONF_PORT],
            entry.data[CONF_USERNAME],
            entry.data[CONF_PASSWORD],
            use_https=entry.data[CONF_USE_HTTPS],
        )
        await async_release_slot(client, int(slot))
    except (HikvisionError, Exception) as err:  # noqa: BLE001
        _LOGGER.warning("could not release push slot on removal: %s", err)
