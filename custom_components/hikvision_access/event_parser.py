"""Turn raw terminal payloads into normalized :class:`AccessEvent` objects.

Three shapes are handled, all confirmed on DS-K1T342MWX (see docs/DISCOVERY):

* **alertStream** — ``multipart/mixed; boundary=MIME_boundary``; each part is a
  JSON envelope with an ``AccessControllerEvent`` object inside.
* **httpHosts push** — ``multipart/form-data``; a JSON part (same envelope, key
  ``AccessControllerEvent`` or ``event_log``) plus optional ``image/jpeg`` parts.
* **AcsEvent search** — flat ``InfoList`` items with ``major`` / ``minor`` / ``time``.

Field-name differences between stream and search are absorbed here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from .const import (
    METHOD_UNKNOWN,
    RESULT_GRANTED,
    RESULT_UNKNOWN,
)
from .event_mapper import map_event, refine_method
from .models import AccessEvent
from .storage import compute_event_uid
from .util import loads_hik

__all__ = [
    "iter_multipart",
    "loads_hik",
    "parse_acs_search_item",
    "parse_boundary",
    "parse_push_body",
    "parse_stream_envelope",
    "parse_timestamp",
]

_LOGGER = logging.getLogger(__name__)

_BOUNDARY_RE = re.compile(r"boundary=(?:\"([^\"]+)\"|([^;]+))", re.I)


def parse_boundary(content_type: str) -> str | None:
    m = _BOUNDARY_RE.search(content_type or "")
    if not m:
        return None
    return m.group(1) or m.group(2).strip()


def _parse_section(chunk: bytes) -> tuple[dict[str, str], bytes] | None:
    chunk = chunk.strip(b"\r\n")
    if not chunk or chunk == b"--":
        return None

    # Split header block from body at the first blank line, preserving body
    # bytes exactly (a JPEG body may contain 0x0D). Tolerates CRLF, bare LF,
    # and the stray CRCRLF some captures introduce.
    head_lines: list[bytes] = []
    body = b""
    offset = 0
    for line in chunk.split(b"\n"):
        offset += len(line) + 1
        if line.strip(b"\r") == b"":
            body = chunk[offset:]
            break
        head_lines.append(line)
    else:
        head_lines, body = [], chunk

    headers: dict[str, str] = {}
    for line in head_lines:
        text = line.decode("latin-1", errors="replace")
        if ":" in text:
            k, v = text.split(":", 1)
            headers[k.strip().lower()] = v.strip()
    return headers, body.strip(b"\r\n")


def iter_multipart(
    body: bytes, boundary: str
) -> Iterator[tuple[dict[str, str], bytes]]:
    """Yield ``(headers, content)`` for each section of a complete multipart body."""
    for chunk in body.split(b"--" + boundary.encode()):
        section = _parse_section(chunk)
        if section is not None:
            yield section


def split_stream_buffer(
    buffer: bytes, boundary: str
) -> tuple[list[tuple[dict[str, str], bytes]], bytes]:
    """Framing for a *growing* alertStream buffer.

    Returns the complete sections found so far and the trailing remainder that
    has not been fully received yet.
    """
    delim = b"--" + boundary.encode()
    pieces = buffer.split(delim)
    remainder = pieces.pop() if pieces else b""
    sections = [s for s in (_parse_section(p) for p in pieces) if s is not None]
    return sections, remainder


# --------------------------------------------------------------------------
# time
# --------------------------------------------------------------------------

def parse_timestamp(value: str | None) -> datetime:
    """ISO-8601 (with or without offset) -> timezone-aware UTC datetime."""
    if not value:
        return datetime.now(UTC)
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _LOGGER.debug("unparseable timestamp %r, using now()", value)
        return datetime.now(UTC)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


# --------------------------------------------------------------------------
# normalization
# --------------------------------------------------------------------------

def _clean(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s in ("undefined", "invalid", "unknown"):
        return None
    return s


def _mask_card(card: str | None, *, mask: bool) -> str | None:
    if not card or not mask:
        return card
    return ("*" * max(0, len(card) - 4)) + card[-4:]


def _build(
    *,
    device_serial: str,
    device_id: str,
    major: Any,
    minor: Any,
    ts: datetime,
    native_serial: Any,
    name: str | None,
    person_id: str | None,
    card: str | None,
    verify_mode: str | None,
    door_id: str | None,
    door_name: str | None,
    picture_url: str | None,
    is_live: bool | None,
    raw: Any,
    mask_card: bool,
) -> AccessEvent:
    mapping = map_event(major, minor)
    result = mapping.access_result
    method = refine_method(mapping, verify_mode)

    # a named person on a decision code with no explicit failure => granted
    if result in (None, RESULT_UNKNOWN) and mapping.is_access_decision and name:
        result = RESULT_GRANTED

    uid = compute_event_uid(
        device_serial,
        native_serial=native_serial,
        major=major,
        minor=minor,
        timestamp=ts,
        person_id=person_id,
    )
    return AccessEvent(
        event_uid=uid,
        device_id=device_id,
        timestamp=ts,
        serial_number=_clean(native_serial),
        door_id=door_id,
        door_name=door_name,
        person_id=person_id,
        person_name=name,
        card_number=_mask_card(card, mask=mask_card),
        authentication_method=method or METHOD_UNKNOWN,
        access_result=result or RESULT_UNKNOWN,
        major_event_type=major,
        minor_event_type=minor,
        event_picture_url=picture_url,
        is_live=is_live,
        raw_payload=raw,
    )


def parse_acs_search_item(
    item: dict[str, Any],
    *,
    device_serial: str,
    device_id: str,
    door_name: str | None = None,
    mask_card: bool = True,
) -> AccessEvent:
    return _build(
        device_serial=device_serial,
        device_id=device_id,
        major=item.get("major"),
        minor=item.get("minor"),
        ts=parse_timestamp(item.get("time")),
        native_serial=item.get("serialNo"),
        name=_clean(item.get("name")),
        person_id=_clean(item.get("employeeNoString") or item.get("employeeNo")),
        card=_clean(item.get("cardNo")),
        verify_mode=item.get("currentVerifyMode"),
        door_id=_clean(item.get("doorNo")),
        door_name=door_name,
        picture_url=_clean(item.get("pictureURL")),
        is_live=False,
        raw=item,
        mask_card=mask_card,
    )


def parse_stream_envelope(
    envelope: dict[str, Any],
    *,
    device_serial: str,
    device_id: str,
    door_name: str | None = None,
    mask_card: bool = True,
) -> AccessEvent | None:
    ace = envelope.get("AccessControllerEvent") or envelope.get("event_log")
    if not isinstance(ace, dict):
        if envelope.get("eventType") not in (None, "AccessControllerEvent"):
            return None
        ace = envelope
    ts = parse_timestamp(envelope.get("dateTime") or ace.get("dateTime"))
    live = ace.get("currentEvent")
    return _build(
        device_serial=device_serial,
        device_id=device_id,
        major=ace.get("majorEventType", ace.get("major")),
        minor=ace.get("subEventType", ace.get("minor")),
        ts=ts,
        native_serial=ace.get("serialNo"),
        name=_clean(ace.get("name")),
        person_id=_clean(ace.get("employeeNoString") or ace.get("employeeNo")),
        card=_clean(ace.get("cardNo")),
        verify_mode=ace.get("currentVerifyMode"),
        door_id=_clean(ace.get("doorNo")),
        door_name=door_name,
        picture_url=_clean(ace.get("pictureURL")),
        is_live=None if live is None else bool(live),
        raw=envelope,
        mask_card=mask_card,
    )


def parse_push_body(
    content_type: str,
    body: bytes,
    *,
    device_serial: str,
    device_id: str,
    door_name: str | None = None,
    mask_card: bool = True,
) -> tuple[AccessEvent | None, bytes | None]:
    """Parse one httpHosts POST. Returns (event, first inline JPEG or None)."""
    boundary = parse_boundary(content_type)
    envelope: dict[str, Any] | None = None
    jpeg: bytes | None = None

    if boundary:
        for headers, content in iter_multipart(body, boundary):
            ctype = headers.get("content-type", "")
            if "json" in ctype or content[:1] in (b"{", b"["):
                try:
                    envelope = loads_hik(content)
                except json.JSONDecodeError:
                    continue
            elif "image/" in ctype or content[:3] == b"\xff\xd8\xff":
                if jpeg is None:
                    jpeg = content
    else:
        try:
            envelope = loads_hik(body)
        except json.JSONDecodeError:
            return None, None

    if not isinstance(envelope, dict):
        return None, jpeg
    event = parse_stream_envelope(
        envelope,
        device_serial=device_serial,
        device_id=device_id,
        door_name=door_name,
        mask_card=mask_card,
    )
    return event, jpeg
