"""Capability discovery via safe feature probing (spec §6).

Rules for interpreting a probe:
    200        -> supported
    404 / 501  -> unsupported
    403        -> unsupported for this account
    401        -> auth problem (propagates, never "unsupported")
    5xx / other-> inconclusive, leave the default
No probe here mutates the device.
"""

from __future__ import annotations

import logging

from .api import HikvisionISAPIClient
from .const import (
    EP_ACS_CAPS,
    EP_ACS_EVENT_CAPS,
    EP_ALERT_STREAM,
    EP_HTTP_HOSTS,
    EP_REMOTE_DOOR_CAPS,
    EP_STREAMING_CHANNELS,
    EP_USER_CAPS,
    EP_USER_COUNT,
    EP_VIDEO_INTERCOM_CAPS,
)
from .exceptions import (
    HikvisionAuthError,
    HikvisionError,
    HikvisionUnsupportedError,
)
from .models import DeviceCapabilities

_LOGGER = logging.getLogger(__name__)


async def _ok(client: HikvisionISAPIClient, endpoint: str) -> tuple[bool, dict]:
    try:
        return True, await client.async_get_caps(endpoint)
    except HikvisionUnsupportedError:
        return False, {}
    except HikvisionAuthError:
        raise
    except HikvisionError as err:
        _LOGGER.debug("probe %s inconclusive: %s", endpoint, err)
        return False, {}


async def async_discover(client: HikvisionISAPIClient) -> DeviceCapabilities:
    caps = DeviceCapabilities()

    acs_ok, acs = await _ok(client, EP_ACS_CAPS)
    if acs_ok:
        ac = acs.get("AccessControl", acs)
        caps.remote_door_control = _flag(ac, "isSupportRemoteControlDoor")
        caps.user_search = _flag(ac, "isSupportUserInfo")
        caps.card = _flag(ac, "isSupportCardInfo")
        caps.fingerprint = _flag(ac, "isSupportFingerPrintCfg")
        caps.face = _flag(ac, "isSupportFaceCompareCond") or True
        caps.raw["acs_flags"] = sorted(
            k for k, v in ac.items() if str(v).lower() == "true"
        )

    ev_ok, ev = await _ok(client, EP_ACS_EVENT_CAPS)
    if ev_ok:
        caps.access_event_search = True
        cond = ev.get("AcsEvent", {}).get("AcsEventCond", {})
        info = ev.get("AcsEvent", {}).get("InfoList", {})
        caps.event_picture = "pictureURL" in info or cond.get("picEnable") is not None

    rc_ok, _ = await _ok(client, EP_REMOTE_DOOR_CAPS)
    caps.remote_door_control = caps.remote_door_control or rc_ok

    uc_ok, uc = await _ok(client, EP_USER_COUNT)
    if uc_ok:
        caps.user_search = True
        count = uc.get("UserInfoCount", {})
        caps.user_picture = int(count.get("bindFaceUserNumber", 0)) >= 0

    us_ok, us = await _ok(client, EP_USER_CAPS)
    if us_ok:
        ui = us.get("UserInfo", {})
        vm = str(ui.get("userVerifyMode", {}).get("@opt", ""))
        caps.face = caps.face or "face" in vm
        caps.card = caps.card or "card" in vm

    # httpHosts is a plain GET; alertStream we confirm without holding open.
    caps.push_notification = await _endpoint_answers(client, EP_HTTP_HOSTS)
    try:
        caps.event_stream = await client.async_probe_stream(EP_ALERT_STREAM)
    except HikvisionAuthError:
        raise
    except HikvisionError:
        caps.event_stream = False

    caps.door_status = acs_ok  # AcsWorkStatus lives alongside; assume with ACS caps
    caps.door_count = 1

    # video (RTSP/snapshot) + video-intercom (call button)
    vid_ok, _ = await _ok(client, EP_STREAMING_CHANNELS)
    caps.video = vid_ok
    ic_ok, ic = await _ok(client, EP_VIDEO_INTERCOM_CAPS)
    if ic_ok:
        node = ic.get("VideoIntercomCap", ic)
        caps.intercom = _flag(node, "isSupportCallStatus") or _flag(
            node, "isSupportCallSignal"
        )

    return caps


async def _endpoint_answers(client: HikvisionISAPIClient, endpoint: str) -> bool:
    try:
        await client._get_text(endpoint, timeout=6)  # noqa: SLF001
        return True
    except HikvisionUnsupportedError:
        return False
    except HikvisionAuthError:
        raise
    except HikvisionError:
        return False


def _flag(d: dict, key: str) -> bool:
    v = d.get(key)
    return str(v).lower() == "true"
