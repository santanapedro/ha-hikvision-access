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
    HikvisionError,
    HikvisionLockoutError,
    HikvisionProtocolError,
    HikvisionStreamBusyError,
    HikvisionTimeoutError,
    HikvisionUnsupportedError,
)
from .models import DeviceInfo

_LOGGER = logging.getLogger(__name__)

_HIK_NS = re.compile(r"\{.*?\}")

_MAX_PICTURE_BYTES = 8 * 1024 * 1024  # event/user JPEGs are tens of KB


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

    __slots__ = ("_nc", "nonce", "opaque", "password", "qop", "realm", "username")

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password
        self.realm = self.nonce = self.opaque = self.qop = ""
        self._nc = 0

    def load_challenge(self, header: str) -> None:
        parts = dict(re.findall(r'(\w+)="?([^",]+)"?', header))
        new_nonce = parts.get("nonce", "")
        self.realm = parts.get("realm", "")
        self.opaque = parts.get("opaque", "")
        self.qop = parts.get("qop", "auth")
        if new_nonce != self.nonce:
            # RFC 2617: nonce-count restarts at 1 for every fresh nonce
            self._nc = 0
        self.nonce = new_nonce

    @property
    def stale(self) -> bool:
        return not self.nonce

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

    # host -> monotonic time the lockout clears. Shared across client
    # instances so a fresh client (e.g. a config-entry setup retry) still
    # respects a lock a previous client discovered.
    _lockouts: dict[str, float] = {}

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
        self._username = username
        self._password = password
        self._timeout = timeout
        # These terminals cap how many times a Digest nonce may be reused
        # (a DS-K1T342MWX on FW V4.48.40 rejects the 3rd use), so we do a fresh
        # challenge per request — exactly like requests' HTTPDigestAuth, which
        # never trips the lock. `_gate` + `_pace` keep bursts polite.
        self._gate = asyncio.Semaphore(4)
        self._min_spacing = 0.05
        self._last_request = 0.0
        self._pace_lock = asyncio.Lock()

    @property
    def lock_remaining(self) -> int:
        until = self._lockouts.get(self._host, 0.0)
        return max(0, int(until - time.monotonic()))

    def _check_lock(self) -> None:
        remaining = self.lock_remaining
        if remaining > 0:
            raise HikvisionLockoutError(remaining)

    def _note_lock(self, unlock_seconds: int | None) -> None:
        # add margin: the terminal extends the lock on every attempt during it
        wait = (unlock_seconds if unlock_seconds and unlock_seconds > 0 else 300) + 30
        self._lockouts[self._host] = max(
            self._lockouts.get(self._host, 0.0), time.monotonic() + wait
        )

    async def _pace(self) -> None:
        async with self._pace_lock:
            wait = self._min_spacing - (time.monotonic() - self._last_request)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last_request = time.monotonic()

    async def _fresh_digest(self, method: str, uri: str) -> str | None:
        """Unauthenticated probe -> parse challenge -> return one Authorization
        header. ``None`` if the endpoint needs no auth."""
        self._check_lock()
        to = aiohttp.ClientTimeout(total=self._timeout)
        async with self._session.get(
            self._base + EP_DEVICE_INFO, timeout=to
        ) as probe:
            if probe.status == 200:
                return None
            body = await probe.text()
            self._raise_if_locked(body)
            challenge = next(
                (c for c in probe.headers.getall("WWW-Authenticate", [])
                 if "Digest" in c),
                "",
            )
        if not challenge:
            raise HikvisionConnectionError(
                f"no Digest challenge from terminal (HTTP {probe.status})"
            )
        digest = _Digest(self._username, self._password)
        digest.load_challenge(challenge)
        return digest.header(method, uri)

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

        self._check_lock()
        try:
            async with self._gate:
                await self._pace()
                auth = await self._fresh_digest(method, uri)
                hdrs = dict(headers)
                if auth:
                    hdrs["Authorization"] = auth
                resp = await self._session.request(
                    method, url, data=data, headers=hdrs, timeout=to
                )
            if resp.status == 401:
                body = await resp.text()
                resp.release()
                _LOGGER.debug("401 on %s: %r", endpoint, body[:200])
                self._raise_if_locked(body)
                raise HikvisionConnectionError(
                    f"{method} {endpoint}: Digest rejected ({body[:120]!r})"
                )
        except HikvisionLockoutError:
            raise
        except aiohttp.ClientConnectorError as err:
            raise HikvisionConnectionError(str(err)) from err
        except TimeoutError as err:
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
    def _parse_lock_seconds(body: str) -> int | None:
        """Seconds of lock signalled by a 401 body, or None if not a lock.

        Careful: ``<lockStatus>unlock</lockStatus>`` also contains ``lock``.
        ``<retryLoginTime>`` (failed-login counter) means the terminal is
        tracking us toward a lock — a wrong password is not distinguishable
        here, and the config flow already validated the credentials.
        """
        if re.search(r"<lockStatus>\s*lock\s*</lockStatus>", body):
            m = re.search(r"<unlockTime>(\d+)</unlockTime>", body)
            return int(m.group(1)) if m else 300
        m = re.search(r"<unlockTime>(\d+)</unlockTime>", body)
        if m and int(m.group(1)) > 0:
            return int(m.group(1))
        if "<retryLoginTime>" in body:
            return 180
        return None

    def _raise_if_locked(self, body: str) -> None:
        """Raise + park the client so no poller keeps refreshing the lock."""
        seconds = self._parse_lock_seconds(body)
        if seconds is None:
            return
        self._note_lock(seconds)
        raise HikvisionLockoutError(seconds)

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

    async def async_get_snapshot(self, channel: int) -> bytes:
        """JPEG snapshot from a streaming channel (101 = main, 102 = sub)."""
        from .const import EP_SNAPSHOT

        resp = await self._request("GET", EP_SNAPSHOT.format(channel=channel))
        try:
            blob = await self._read_capped(resp)
        finally:
            resp.release()
        if blob[:3] != b"\xff\xd8\xff":
            raise HikvisionProtocolError("snapshot is not a JPEG")
        return blob

    async def async_get_call_status(self) -> str | None:
        """Video-intercom call state: 'idle', 'ring', 'onCall', ... or None."""
        from .const import EP_CALL_STATUS

        try:
            data = await self.async_get_caps(EP_CALL_STATUS)
        except HikvisionUnsupportedError:
            return None
        node = data.get("CallStatus", data)
        return node.get("status")

    def rtsp_url(self, channel: int, port: int) -> str:
        """RTSP URL for HA's stream component (credentials embedded)."""
        from urllib.parse import quote

        from .const import RTSP_PATH

        user = quote(self._username, safe="")
        pw = quote(self._password, safe="")
        return f"rtsp://{user}:{pw}@{self._host}:{port}{RTSP_PATH.format(channel=channel)}"

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
        if url_or_path.startswith("http") and self._host in url_or_path:
            endpoint = url_or_path.split(self._host, 1)[-1]
        resp = await self._request("GET", endpoint)
        try:
            return await self._read_capped(resp)
        finally:
            resp.release()

    @staticmethod
    async def _read_capped(resp: aiohttp.ClientResponse, cap: int = _MAX_PICTURE_BYTES) -> bytes:
        """Read a response body but never more than ``cap`` (a face JPEG is ~40 KB;
        this only guards against a terminal that answers with something huge)."""
        if (resp.content_length or 0) > cap:
            raise HikvisionProtocolError(f"response too large ({resp.content_length} bytes)")
        chunks: list[bytes] = []
        total = 0
        async for chunk in resp.content.iter_chunked(65536):
            total += len(chunk)
            if total > cap:
                raise HikvisionProtocolError("response exceeded size cap while reading")
            chunks.append(chunk)
        return b"".join(chunks)

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
        acknowledged = (
            "<statusCode>1</statusCode>" in body
            or '"statusCode":\t1' in body
            or "OK" in body
        )
        if not acknowledged:
            raise HikvisionProtocolError(f"door command not acknowledged: {body[:200]}")

    async def async_probe_stream(self, endpoint: str) -> bool:
        """Confirm the alertStream endpoint answers 200 without holding it open."""
        to = aiohttp.ClientTimeout(total=8, sock_connect=STREAM_CONNECT_TIMEOUT_S)
        try:
            header = await self._fresh_digest("GET", endpoint)
            async with self._session.get(
                self._base + endpoint,
                headers={"Authorization": header} if header else {},
                timeout=to,
            ) as resp:
                if resp.status == 401:
                    self._raise_if_locked(await resp.text())
                    return False
                return resp.status == 200
        except TimeoutError:
            return True  # connected, just no data yet — the stream exists
        except (aiohttp.ClientError, HikvisionError):
            return False

    @asynccontextmanager
    async def async_stream(
        self, endpoint: str, idle_timeout: float
    ) -> AsyncIterator[aiohttp.ClientResponse]:
        """Open a persistent alertStream GET (spec §9). Caller iterates the body."""
        to = aiohttp.ClientTimeout(
            total=None, sock_connect=STREAM_CONNECT_TIMEOUT_S, sock_read=idle_timeout
        )

        async def _open() -> aiohttp.ClientResponse:
            header = await self._fresh_digest("GET", endpoint)
            return await self._session.get(
                self._base + endpoint,
                headers={"Authorization": header} if header else {},
                timeout=to,
            )

        try:
            resp = await _open()
            if resp.status == 401:
                body = await resp.text()
                resp.release()
                _LOGGER.debug("stream 401: %r", body[:200])
                self._raise_if_locked(body)
                await asyncio.sleep(1)
                resp = await _open()
            # The terminal answers 404 on alertStream while a previous
            # connection's slot is still held — wait it out once.
            if resp.status == 404:
                resp.release()
                await asyncio.sleep(3)
                resp = await _open()
        except aiohttp.ClientError as err:
            raise HikvisionConnectionError(str(err)) from err
        try:
            if resp.status == 401:
                self._raise_if_locked(await resp.text())
                raise HikvisionConnectionError("stream Digest handshake did not settle")
            if resp.status == 404:
                await resp.text()
                # slot still busy — tell the listener to wait a good while
                raise HikvisionStreamBusyError()
            if resp.status != 200:
                text = await resp.text()
                raise HikvisionProtocolError(f"stream HTTP {resp.status}: {text[:120]}")
            yield resp
        finally:
            # a partially-read stream must be closed, not just released to the pool
            resp.close()
