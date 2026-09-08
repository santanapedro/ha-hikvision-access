"""Async ISAPI client for Hikvision access terminals (spec §5).

- HTTPS + HTTP Digest (implemented here; aiohttp has no built-in digest).
- Never falls back to Basic auth: extra 401s trip the terminal's login lockout.
- Detects the lockout reply and raises :class:`HikvisionLockoutError`.
- Understands Hikvision's XML/JSON error envelopes.

Only the calls the integration actually needs are exposed. There is no generic
"send raw ISAPI" method (spec §30).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from xml.etree import ElementTree as ET

import aiohttp

from .const import (
    ACS_EVENT_MAX_RESULTS,
    DEFAULT_REQUEST_TIMEOUT_S,
    EP_ACS_EVENT,
    EP_DEVICE_INFO,
    EP_HTTP_HOSTS,
    EP_REMOTE_DOOR,
    EP_SYSTEM_TIME,
    EP_USER_SEARCH,
    STREAM_CONNECT_TIMEOUT_S,
)
from .exceptions import (
    HikvisionAuthError,
    HikvisionConnectionError,
    HikvisionLockoutError,
    HikvisionProtocolError,
    HikvisionTimeoutError,
    HikvisionUnsupportedError,
)
from .models import DeviceInfo

_LOGGER = logging.getLogger(__name__)

_HIK_NS = re.compile(r"\{.*?\}")


def _strip_ns(tag: str) -> str:
    return _HIK_NS.sub("", tag)


def _xml_to_dict(text: str) -> dict[str, Any]:
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise HikvisionProtocolError(f"invalid XML: {err}") from err
    return {_strip_ns(child.tag): (child.text or "").strip() for child in root}


class _Digest:
    """Minimal RFC 2617 Digest (qop=auth, MD5) — what these terminals use."""

    __slots__ = ("username", "password", "realm", "nonce", "opaque", "qop", "_nc")

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.realm = self.nonce = self.opaque = self.qop = ""
        self._nc = 0

    def load_challenge(self, header: str) -> None:
        parts = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
        self.realm = parts.get("realm", "")
        self.nonce = parts.get("nonce", "")
        self.opaque = parts.get("opaque", "")
        self.qop = parts.get("qop", "auth")

    def header(self, method: str, uri: str) -> str:
        self._nc += 1
        nc = f"{self._nc:08x}"
        cnonce = hashlib.md5(f"{time.time()}{nc}".encode()).hexdigest()[:16]
        ha1 = hashlib.md5(
            f"{self.username}:{self.realm}:{self.password}".encode()
        ).hexdigest()
        ha2 = hashlib.md5(f"{method}:{uri}".encode()).hexdigest()
        response = hashlib.md5(
            f"{ha1}:{self.nonce}:{nc}:{cnonce}:auth:{ha2}".encode()
        ).hexdigest()
        fields = [
            f'username="{self.username}"',
            f'realm="{self.realm}"',
            f'nonce="{self.nonce}"',
            f'uri="{uri}"',
            "qop=auth",
            f"nc={nc}",
            f'cnonce="{cnonce}"',
            f'response="{response}"',
        ]
        if self.opaque:
            fields.append(f'opaque="{self.opaque}"')
        return "Digest " + ", ".join(fields)


class HikvisionISAPIClient:
    """One authenticated client for one terminal."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        port: int,
        username: str,
        password: str,
        *,
        use_https: bool = True,
        timeout: float = DEFAULT_REQUEST_TIMEOUT_S,
    ) -> None:
        scheme = "https" if use_https else "http"
        self._session = session
        self._base = f"{scheme}://{host}:{port}"
        self._host = host
        self._timeout = timeout
        self._digest = _Digest(username, password)
        self._auth_lock = asyncio.Lock()

    # ---- low level -------------------------------------------------------

    async def _request(
        self,
        method: str,
        endpoint: str,
        *,
        data: str | bytes | None = None,
        json_body: Any | None = None,
        timeout: float | None = None,
        allow_status: tuple[int, ...] = (),
    ) -> aiohttp.ClientResponse:
        url = self._base + endpoint
        uri = endpoint  # digest uri is the path+query, no scheme/host
        headers: dict[str, str] = {}
        if json_body is not None:
            import json as _json

            data = _json.dumps(json_body)
            headers["Content-Type"] = "application/json"

        to = aiohttp.ClientTimeout(total=timeout or self._timeout)

        async def _send() -> aiohttp.ClientResponse:
            hdrs = dict(headers)
            if self._digest.nonce:
                hdrs["Authorization"] = self._digest.header(method, uri)
            return await self._session.request(
                method, url, data=data, headers=hdrs, timeout=to
            )

        try:
            resp = await _send()
            if resp.status == 401:
                body = await resp.text()
                self._raise_if_locked(body)
                challenge = resp.headers.get("WWW-Authenticate", "")
                if "Digest" not in challenge:
                    raise HikvisionAuthError("no Digest challenge from terminal")
                async with self._auth_lock:
                    self._digest.load_challenge(challenge)
                resp.release()
                resp = await _send()
                if resp.status == 401:
                    self._raise_if_locked(await resp.text())
                    raise HikvisionAuthError("credentials rejected")
        except aiohttp.ClientConnectorError as err:
            raise HikvisionConnectionError(str(err)) from err
        except asyncio.TimeoutError as err:
            raise HikvisionTimeoutError(f"{method} {endpoint} timed out") from err
        except aiohttp.ClientError as err:
            raise HikvisionConnectionError(str(err)) from err

        if resp.status == 403:
            raise HikvisionAuthError(f"forbidden: {endpoint}")
        if resp.status in (404, 501):
            resp.release()
            raise HikvisionUnsupportedError(endpoint)
        if resp.status >= 400 and resp.status not in allow_status:
            text = await resp.text()
            raise HikvisionProtocolError(f"HTTP {resp.status} on {endpoint}: {text[:200]}")
        return resp

    @staticmethod
    def _raise_if_locked(body: str) -> None:
        if "lockStatus" in body and "lock" in body:
            m = re.search(r"<unlockTime>(\d+)</unlockTime>", body)
            raise HikvisionLockoutError(int(m.group(1)) if m else None)

    async def _get_text(self, endpoint: str, **kw: Any) -> str:
        resp = await self._request("GET", endpoint, **kw)
        try:
            return await resp.text()
        finally:
            resp.release()

    async def _get_json(self, endpoint: str, **kw: Any) -> dict[str, Any]:
        resp = await self._request("GET", endpoint, **kw)
        try:
            return await resp.json(content_type=None)
        except ValueError as err:
            raise HikvisionProtocolError(f"invalid JSON from {endpoint}") from err
        finally:
            resp.release()

    # ---- public API ----------------------------------------------------

    async def async_get_device_info(self) -> DeviceInfo:
        d = _xml_to_dict(await self._get_text(EP_DEVICE_INFO))
        if "model" not in d or "serialNumber" not in d:
            raise HikvisionProtocolError("deviceInfo missing model/serialNumber")
        return DeviceInfo(
            model=d["model"],
            serial_number=d["serialNumber"],
            firmware=d.get("firmwareVersion", "unknown"),
            mac=d.get("macAddress"),
            device_name=d.get("deviceName"),
            device_type=d.get("deviceType"),
            sub_device_type=d.get("subDeviceType"),
            electro_lock_num=int(d.get("electroLockNum", "1") or "1"),
        )

    async def async_get_time(self) -> str:
        return _xml_to_dict(await self._get_text(EP_SYSTEM_TIME)).get("localTime", "")

    async def async_get_json(self, endpoint: str) -> dict[str, Any]:
        """Generic GET for capability probing (capabilities.py)."""
        return await self._get_json(endpoint)

    async def async_get_caps(self, endpoint: str) -> dict[str, Any]:
        """GET a capabilities endpoint, tolerating JSON *or* XML.

        Several ``?format=json`` capability endpoints on this firmware still
        answer XML. For probing we only need the root-level ``isSupport*``
        flags, so a shallow XML->dict is enough; JSON is returned as-is.
        """
        resp = await self._request("GET", endpoint)
        try:
            text = await resp.text()
        finally:
            resp.release()
        # This firmware sometimes sends XML with a "Content-Type: application/json"
        # header, so sniff the body instead of trusting the header.
        if text.lstrip().startswith("<"):
            return _xml_to_dict(text)
        import json as _json

        try:
            return _json.loads(text)
        except ValueError as err:
            raise HikvisionProtocolError(f"unparseable capabilities from {endpoint}") from err

    async def async_search_acs_events(
        self,
        search_id: str,
        start_iso: str,
        end_iso: str,
        *,
        position: int = 0,
        max_results: int = ACS_EVENT_MAX_RESULTS,
        begin_serial_no: int | None = None,
    ) -> dict[str, Any]:
        """One page of ``POST /ISAPI/AccessControl/AcsEvent`` (spec §10)."""
        cond: dict[str, Any] = {
            "searchID": search_id,
            "searchResultPosition": position,
            "maxResults": min(max_results, ACS_EVENT_MAX_RESULTS),
            "major": 0,
            "minor": 0,
            "startTime": start_iso,
            "endTime": end_iso,
        }
        if begin_serial_no is not None:
            # this firmware rejects beginSerialNo without a matching endSerialNo
            cond["beginSerialNo"] = begin_serial_no
            cond["endSerialNo"] = 3_000_000_000
        resp = await self._request("POST", EP_ACS_EVENT, json_body={"AcsEventCond": cond})
        try:
            raw = await resp.read()
        finally:
            resp.release()
        from .util import loads_hik

        try:
            data = loads_hik(raw)
        except ValueError as err:
            raise HikvisionProtocolError("AcsEvent: invalid JSON") from err
        return data.get("AcsEvent", {})

    async def async_search_users(
        self,
        search_id: str,
        *,
        employee_nos: list[str] | None = None,
        position: int = 0,
        max_results: int = 30,
    ) -> dict[str, Any]:
        """One page of ``POST /ISAPI/AccessControl/UserInfo/Search`` (spec §14)."""
        cond: dict[str, Any] = {
            "searchID": search_id,
            "searchResultPosition": position,
            "maxResults": min(max_results, 30),
        }
        if employee_nos:
            cond["EmployeeNoList"] = [{"employeeNo": e} for e in employee_nos]
        resp = await self._request(
            "POST", EP_USER_SEARCH, json_body={"UserInfoSearchCond": cond}
        )
        try:
            raw = await resp.read()
        finally:
            resp.release()
        from .util import loads_hik

        try:
            data = loads_hik(raw)
        except ValueError as err:
            raise HikvisionProtocolError("UserInfo/Search: invalid JSON") from err
        return data.get("UserInfoSearch", {})

    async def async_get_bytes(self, url_or_path: str) -> bytes:
        """Download an event/user picture. Accepts an absolute URL or a path."""
        endpoint = url_or_path
        if url_or_path.startswith("http"):
            endpoint = url_or_path.split(self._host, 1)[-1] if self._host in url_or_path else url_or_path
        resp = await self._request("GET", endpoint)
        try:
            return await resp.read()
        finally:
            resp.release()

    async def async_get_http_hosts(self) -> list[dict[str, Any]]:
        """Parse the httpHosts notification list (id + url + ip + port per slot)."""
        text = await self._get_text(EP_HTTP_HOSTS)
        try:
            root = ET.fromstring(text)
        except ET.ParseError as err:
            raise HikvisionProtocolError(f"httpHosts: {err}") from err
        hosts: list[dict[str, Any]] = []
        for node in root:
            if _strip_ns(node.tag) != "HttpHostNotification":
                continue
            host = {_strip_ns(c.tag): (c.text or "").strip() for c in node}
            hosts.append(host)
        return hosts

    async def async_put_http_host(self, slot: int, xml_body: str) -> None:
        """Replace ONE httpHosts slot, leaving the others untouched."""
        resp = await self._request(
            "PUT", f"{EP_HTTP_HOSTS}/{slot}", data=xml_body
        )
        try:
            body = await resp.text()
        finally:
            resp.release()
        if "<statusString>OK</statusString>" not in body and "statusCode>1<" not in body:
            raise HikvisionProtocolError(f"httpHosts slot {slot} not accepted: {body[:200]}")

    async def async_remote_door(self, door: int, command: str) -> None:
        """PUT RemoteControl/door — physical action, callers must be explicit (spec §30)."""
        if command not in ("open", "close", "alwaysOpen", "alwaysClose"):
            raise ValueError(f"unsupported door command: {command}")
        payload = (
            '<RemoteControlDoor version="2.0" '
            'xmlns="http://www.isapi.org/ver20/XMLSchema">'
            f"<cmd>{command}</cmd></RemoteControlDoor>"
        )
        resp = await self._request(
            "PUT", EP_REMOTE_DOOR.format(door=door), data=payload
        )
        try:
            body = await resp.text()
        finally:
            resp.release()
        if "<statusCode>1</statusCode>" not in body and '"statusCode":\t1' not in body and "OK" not in body:
            raise HikvisionProtocolError(f"door command not acknowledged: {body[:200]}")

    async def async_probe_stream(self, endpoint: str) -> bool:
        """Confirm the alertStream endpoint answers 200 without holding it open."""
        if not self._digest.nonce:
            await self._get_text(EP_DEVICE_INFO)
        to = aiohttp.ClientTimeout(total=8, sock_connect=STREAM_CONNECT_TIMEOUT_S)
        headers = {"Authorization": self._digest.header("GET", endpoint)}
        try:
            async with self._session.get(
                self._base + endpoint, headers=headers, timeout=to
            ) as resp:
                if resp.status == 401:
                    self._raise_if_locked(await resp.text())
                    raise HikvisionAuthError("stream auth rejected")
                return resp.status == 200
        except asyncio.TimeoutError:
            return True  # connected, just no data yet — the stream exists
        except aiohttp.ClientError:
            return False

    @asynccontextmanager
    async def async_stream(
        self, endpoint: str, idle_timeout: float
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open a persistent alertStream GET (spec §9). Caller iterates the body."""
        if not self._digest.nonce:
            await self._get_text(EP_DEVICE_INFO)
        to = aiohttp.ClientTimeout(
            total=None, sock_connect=STREAM_CONNECT_TIMEOUT_S, sock_read=idle_timeout
        )
        headers = {"Authorization": self._digest.header("GET", endpoint)}
        try:
            resp = await self._session.get(
                self._base + endpoint, headers=headers, timeout=to
            )
        except aiohttp.ClientError as err:
            raise HikvisionConnectionError(str(err)) from err
        try:
            if resp.status == 401:
                self._raise_if_locked(await resp.text())
                raise HikvisionAuthError("stream auth rejected")
            if resp.status != 200:
                raise HikvisionProtocolError(f"stream HTTP {resp.status}")
            yield resp
        finally:
            resp.release()
