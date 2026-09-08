"""Internal data models (spec §6, §7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class DeviceInfo:
    """Parsed ``/ISAPI/System/deviceInfo``."""

    model: str
    serial_number: str
    firmware: str
    mac: str | None = None
    device_name: str | None = None
    device_type: str | None = None
    sub_device_type: str | None = None
    electro_lock_num: int = 1


@dataclass(slots=True)
class DeviceCapabilities:
    """What the connected terminal actually supports (spec §6)."""

    event_stream: bool = False
    push_notification: bool = False
    access_event_search: bool = False
    event_picture: bool = False
    user_picture: bool = False
    remote_door_control: bool = False
    door_status: bool = False
    user_search: bool = False
    face: bool = False
    card: bool = False
    fingerprint: bool = False
    qr: bool = False
    door_count: int = 1
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AccessEvent:
    """One normalized access event, from either the stream or a search (spec §7)."""

    event_uid: str
    device_id: str
    timestamp: datetime

    serial_number: str | None = None
    door_id: str | None = None
    door_name: str | None = None

    person_id: str | None = None
    person_name: str | None = None
    card_number: str | None = None

    authentication_method: str | None = None
    access_result: str = "unknown"

    major_event_type: int | str | None = None
    minor_event_type: int | str | None = None

    event_picture_url: str | None = None
    event_picture_path: str | None = None
    user_picture_path: str | None = None

    is_live: bool | None = None
    raw_payload: dict[str, Any] | str | None = None
