"""Map raw Hikvision (major, minor) event codes to normalized meaning.

The application must never branch on raw Hikvision numbers directly (spec §7,
§50). Everything goes through here.

Codes below were confirmed on a **DS-K1T342MWX / FW V3.16.1** by tallying
~6300 real events (see docs/DISCOVERY-DS-K1T342MWX.md). Unknown codes are
returned as ``UNKNOWN_MAPPING`` and must be logged, never dropped silently.
"""

from __future__ import annotations

from dataclasses import dataclass

from .const import (
    EVENT_ACCESS_DENIED,
    EVENT_ACCESS_GRANTED,
    EVENT_DEVICE_ALARM,
    EVENT_DOOR_CLOSED,
    EVENT_DOOR_OPENED,
    EVENT_TAMPER,
    METHOD_BUTTON,
    METHOD_CARD,
    METHOD_FACE,
    METHOD_FINGERPRINT,
    METHOD_OTHER,
    METHOD_PASSWORD,
    METHOD_REMOTE,
    METHOD_UNKNOWN,
    RESULT_DENIED,
    RESULT_GRANTED,
    RESULT_UNKNOWN,
)


@dataclass(frozen=True, slots=True)
class EventMapping:
    """Normalized meaning of a raw (major, minor) pair."""

    entity_event: str | None          # one of EVENT_* or None if not entity-worthy
    access_result: str | None         # RESULT_* or None if not an access decision
    method: str | None                # METHOD_* or None
    key: str                          # stable slug for logs / translations
    is_access_decision: bool = False


UNKNOWN_MAPPING = EventMapping(
    entity_event=None, access_result=RESULT_UNKNOWN, method=METHOD_UNKNOWN,
    key="unknown",
)

# Hikvision "major" categories
MAJOR_ALARM = 1
MAJOR_EXCEPTION = 2
MAJOR_OPERATION = 3
MAJOR_EVENT = 5  # access-control events live here on this family

# (major, minor) -> EventMapping
_MAP: dict[tuple[int, int], EventMapping] = {
    # ---- access decisions (major 5) ----
    (5, 75): EventMapping(EVENT_ACCESS_GRANTED, RESULT_GRANTED, METHOD_FACE,
                          "face_verified", is_access_decision=True),
    (5, 76): EventMapping(EVENT_ACCESS_DENIED, RESULT_DENIED, METHOD_FACE,
                          "face_failed", is_access_decision=True),
    (5, 1): EventMapping(EVENT_ACCESS_GRANTED, RESULT_GRANTED, METHOD_CARD,
                         "card_verified", is_access_decision=True),
    (5, 38): EventMapping(EVENT_ACCESS_GRANTED, RESULT_GRANTED, METHOD_FINGERPRINT,
                          "fingerprint_verified", is_access_decision=True),
    (5, 19): EventMapping(EVENT_ACCESS_GRANTED, RESULT_GRANTED, METHOD_PASSWORD,
                          "password_verified", is_access_decision=True),
    # ---- door / relay lifecycle (major 5) ----
    (5, 21): EventMapping(None, None, None, "door_unlocked"),
    (5, 22): EventMapping(None, None, None, "door_locked"),
    (5, 23): EventMapping(EVENT_DOOR_OPENED, None, None, "door_open_contact"),
    (5, 24): EventMapping(EVENT_DOOR_CLOSED, None, None, "door_closed_contact"),
    (5, 31): EventMapping(EVENT_ACCESS_GRANTED, RESULT_GRANTED, METHOD_BUTTON,
                          "exit_button", is_access_decision=True),
    # ---- remote operation (major 3) ----
    (3, 112): EventMapping(None, None, METHOD_REMOTE, "remote_login"),
    (3, 113): EventMapping(None, None, METHOD_REMOTE, "remote_logout"),
    (3, 1024): EventMapping(None, None, METHOD_REMOTE, "remote_operation"),
    (3, 1029): EventMapping(None, None, None, "operation_event"),
    # ---- exceptions (major 2) ----
    (2, 39): EventMapping(EVENT_DEVICE_ALARM, None, None, "device_exception"),
    (2, 1024): EventMapping(EVENT_DEVICE_ALARM, None, None, "device_exception"),
    (2, 1031): EventMapping(EVENT_DEVICE_ALARM, None, None, "device_exception"),
    # ---- tamper (major 1) ----
    (1, 1028): EventMapping(EVENT_TAMPER, None, None, "tamper"),
}

# verifyMode string (from event body) -> normalized method, when the code alone
# is ambiguous (e.g. "cardOrFace" that resolved to a face read).
_VERIFY_MODE_METHOD: dict[str, str] = {
    "face": METHOD_FACE,
    "card": METHOD_CARD,
    "fp": METHOD_FINGERPRINT,
    "fingerprint": METHOD_FINGERPRINT,
    "pw": METHOD_PASSWORD,
    "password": METHOD_PASSWORD,
    "localPassword": METHOD_PASSWORD,
}


def map_event(major: int | str | None, minor: int | str | None) -> EventMapping:
    """Return the normalized meaning of a raw (major, minor) pair."""
    try:
        key = (int(major), int(minor))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return UNKNOWN_MAPPING
    return _MAP.get(key, UNKNOWN_MAPPING)


def refine_method(mapping: EventMapping, verify_mode: str | None) -> str:
    """Prefer the event's own verifyMode over the code's default method."""
    if verify_mode:
        vm = verify_mode.strip().lower()
        for token, method in _VERIFY_MODE_METHOD.items():
            if token.lower() in vm:
                return method
    return mapping.method or METHOD_OTHER


def is_known(major: int | str | None, minor: int | str | None) -> bool:
    try:
        return (int(major), int(minor)) in _MAP  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return False
