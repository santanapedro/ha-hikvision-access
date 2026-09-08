"""httpHosts push route (spec §4 rota B, §24).

Two parts:

* :class:`HikvisionPushView` — an unauthenticated HTTP endpoint the terminal
  POSTs events to. It cannot present Home Assistant credentials, so the URL
  carries a per-entry random token and we check it constant-time.
* :func:`async_claim_slot` / :func:`async_release_slot` — write ONE free
  ``httpHosts`` slot on the terminal to point at that URL. This is a standing
  configuration change on the device, so it only runs when the user opts in
  (option ``register_push_on_device``) or calls the ``setup_push`` service.
"""

from __future__ import annotations

import hmac
import logging
import secrets
from urllib.parse import urlparse

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .api import HikvisionISAPIClient
from .event_parser import parse_push_body
from .exceptions import HikvisionError
from .gateway import EventGateway

_LOGGER = logging.getLogger(__name__)

PUSH_URL_FORMAT = "/api/hikvision_access/push/{token}"
_TOTAL_SLOTS = 2


def new_token() -> str:
    return secrets.token_urlsafe(24)


class HikvisionPushView(HomeAssistantView):
    url = "/api/hikvision_access/push/{token}"
    name = "api:hikvision_access:push"
    requires_auth = False

    def __init__(self) -> None:
        # token -> (gateway, device_serial, device_id, door_name_getter)
        self._targets: dict[str, dict] = {}

    def register_target(
        self,
        token: str,
        gateway: EventGateway,
        device_serial: str,
        device_id: str,
        door_name: str | None,
    ) -> None:
        self._targets[token] = {
            "gateway": gateway,
            "serial": device_serial,
            "device_id": device_id,
            "door": door_name,
        }

    def unregister_target(self, token: str) -> None:
        self._targets.pop(token, None)

    @property
    def active(self) -> bool:
        return bool(self._targets)

    async def post(self, request: web.Request, token: str) -> web.Response:
        target = None
        for known, value in self._targets.items():
            if hmac.compare_digest(known, token):
                target = value
                break
        if target is None:
            return web.Response(status=404)

        body = await request.read()
        try:
            event, jpeg = parse_push_body(
                request.headers.get("Content-Type", ""),
                body,
                device_serial=target["serial"],
                device_id=target["device_id"],
                door_name=target["door"],
                mask_card=target["gateway"].mask_card,
            )
        except Exception:
            _LOGGER.exception("failed parsing push body (%d bytes)", len(body))
            return web.Response(status=200)  # never make the terminal retry-storm

        if event is not None:
            gateway: EventGateway = target["gateway"]
            if jpeg:
                gateway.hass.async_create_task(_store_inline_jpeg(gateway, event, jpeg))
            else:
                gateway.hass.async_create_task(
                    gateway.async_handle(event, source="push")
                )
        return web.Response(status=200)


async def _store_inline_jpeg(gateway: EventGateway, event, jpeg: bytes) -> None:
    images = gateway.images
    if images is not None and jpeg[:3] == b"\xff\xd8\xff":
        path = await images.async_save_inline(event, jpeg)
        if path:
            event.event_picture_path = path
    await gateway.async_handle(event, source="push")


def _ha_ip_port(hass: HomeAssistant) -> tuple[str, int]:
    url = get_url(hass, prefer_external=False, allow_ip=True, require_current_request=False)
    parsed = urlparse(url)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if not parsed.hostname:
        raise NoURLAvailableError
    return parsed.hostname, port


def _build_host_xml(slot: int, token: str, ip: str, port: int) -> str:
    return (
        '<HttpHostNotification xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f"<id>{slot}</id>"
        f"<url>{PUSH_URL_FORMAT.format(token=token)}</url>"
        "<protocolType>HTTP</protocolType>"
        "<parameterFormatType>json</parameterFormatType>"
        "<addressingFormatType>ipaddress</addressingFormatType>"
        f"<ipAddress>{ip}</ipAddress>"
        f"<portNo>{port}</portNo>"
        "<httpAuthenticationMethod>none</httpAuthenticationMethod>"
        "<SubscribeEvent><heartbeat>30</heartbeat><eventMode>all</eventMode>"
        "<EventList><Event><type>AccessControllerEvent</type>"
        "<pictureURLType>binary</pictureURLType></Event></EventList>"
        "</SubscribeEvent>"
        "</HttpHostNotification>"
    )


def _empty_host_xml(slot: int) -> str:
    return (
        '<HttpHostNotification xmlns="http://www.isapi.org/ver20/XMLSchema">'
        f"<id>{slot}</id><url></url><protocolType>HTTP</protocolType>"
        "<addressingFormatType>ipaddress</addressingFormatType>"
        "<ipAddress>0.0.0.0</ipAddress><portNo>0</portNo>"
        "<httpAuthenticationMethod>none</httpAuthenticationMethod>"
        "</HttpHostNotification>"
    )


async def async_claim_slot(
    hass: HomeAssistant, client: HikvisionISAPIClient, token: str
) -> int:
    """Write a free httpHosts slot to point at our push view. Returns the slot id.

    A slot is 'free' when its url is empty or already one of ours
    (``/api/hikvision_access/push/``). Never overwrites a slot in use by
    something else.
    """
    ip, port = _ha_ip_port(hass)
    hosts = {int(h.get("id", 0)): h for h in await client.async_get_http_hosts()}

    chosen: int | None = None
    for slot in range(1, _TOTAL_SLOTS + 1):
        host = hosts.get(slot, {})
        url = host.get("url", "")
        proto = host.get("protocolType", "")
        if not url or url.startswith("/api/hikvision_access/push/") or proto == "EHome":
            chosen = slot
            break
    if chosen is None:
        raise HikvisionError(
            "todos os slots de notificação do terminal estão ocupados; "
            "libere um ou use a rota 'stream'"
        )

    await client.async_put_http_host(chosen, _build_host_xml(chosen, token, ip, port))
    _LOGGER.info("claimed httpHosts slot %d -> %s:%d", chosen, ip, port)
    return chosen


async def async_release_slot(
    client: HikvisionISAPIClient, slot: int
) -> None:
    hosts = {int(h.get("id", 0)): h for h in await client.async_get_http_hosts()}
    host = hosts.get(slot, {})
    if host.get("url", "").startswith("/api/hikvision_access/push/"):
        await client.async_put_http_host(slot, _empty_host_xml(slot))
        _LOGGER.info("released httpHosts slot %d", slot)
