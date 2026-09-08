"""Parser tests driven by real (redacted) DS-K1T342MWX payloads."""

import json
from pathlib import Path

from hikvision_access.event_parser import (
    parse_acs_search_item,
    parse_push_body,
    parse_stream_envelope,
    parse_timestamp,
    split_stream_buffer,
)

FIX = Path(__file__).parent / "fixtures"
SERIAL = "DS-K1T342MWX-TEST"


def _acs_items():
    data = json.loads((FIX / "acs_event_search.json").read_text(encoding="utf-8"))
    return data["AcsEvent"]["InfoList"]


def test_acs_face_event_is_granted_with_person_and_picture():
    face = next(i for i in _acs_items() if i["minor"] == 75)
    ev = parse_acs_search_item(face, device_serial=SERIAL, device_id="d1")
    assert ev.access_result == "granted"
    assert ev.authentication_method == "face"
    assert ev.person_id is not None
    assert ev.event_picture_url and ev.event_picture_url.endswith("11")
    assert ev.serial_number == str(face["serialNo"])
    assert ev.event_uid == f"{SERIAL}:{face['serialNo']}"
    assert ev.is_live is False


def test_acs_door_event_has_no_person():
    door = next(i for i in _acs_items() if i["minor"] in (21, 22))
    ev = parse_acs_search_item(door, device_serial=SERIAL, device_id="d1")
    assert ev.person_id is None
    assert ev.access_result == "unknown"


def test_stream_envelope_from_real_multipart():
    raw = (FIX / "alertStream.sample.txt").read_bytes()
    boundary = "MIME_boundary"
    sections, remainder = split_stream_buffer(raw, boundary)
    assert sections, "should have parsed complete sections"

    parsed = []
    for headers, content in sections:
        if content[:1] != b"{":
            continue
        env = json.loads(content.decode("utf-8", "replace"))
        ev = parse_stream_envelope(env, device_serial=SERIAL, device_id="d1")
        if ev:
            parsed.append(ev)
    assert parsed
    face = next((e for e in parsed if str(e.minor_event_type) == "75"), None)
    assert face is not None
    assert face.person_id is not None
    assert face.is_live is False  # historical replay in the capture


def test_stream_buffer_keeps_incomplete_remainder():
    boundary = "b"
    complete = b"--b\r\nContent-Type: application/json\r\n\r\n{\"a\":1}\r\n"
    partial = b"--b\r\nContent-Type: application/json\r\n\r\n{\"a\""
    sections, remainder = split_stream_buffer(complete + partial, boundary)
    assert len(sections) == 1
    assert b'{"a"' in remainder and b'":1}' not in remainder


def test_push_body_json_only():
    env = {
        "eventType": "AccessControllerEvent",
        "dateTime": "2026-09-08T07:42:18-04:00",
        "AccessControllerEvent": {
            "majorEventType": 5, "subEventType": 75, "name": "Fulano",
            "employeeNoString": "25", "currentVerifyMode": "cardOrFace",
            "serialNo": 123, "currentEvent": True, "doorNo": 1,
        },
    }
    body = json.dumps(env).encode()
    ev, jpeg = parse_push_body("application/json", body, device_serial=SERIAL, device_id="d1")
    assert ev is not None and ev.access_result == "granted" and ev.is_live is True
    assert jpeg is None


def test_push_body_multipart_with_jpeg():
    env = {"AccessControllerEvent": {"majorEventType": 5, "subEventType": 75,
                                     "employeeNoString": "9", "serialNo": 5,
                                     "currentEvent": True}}
    jpg = b"\xff\xd8\xff" + b"\x00" * 50
    body = (
        b"--X\r\nContent-Type: application/json\r\n\r\n"
        + json.dumps(env).encode()
        + b"\r\n--X\r\nContent-Type: image/jpeg\r\n\r\n"
        + jpg
        + b"\r\n--X--\r\n"
    )
    ev, jpeg = parse_push_body(
        "multipart/form-data; boundary=X", body, device_serial=SERIAL, device_id="d1"
    )
    assert ev is not None
    assert jpeg == jpg


def test_timestamp_to_utc():
    dt = parse_timestamp("2026-09-08T07:42:18-04:00")
    assert dt.tzinfo is not None
    assert dt.hour == 11  # -04:00 -> UTC


def test_accented_utf8_name_passes_through():
    # the terminal sends correct UTF-8 (b"C\xc3\x81SSIA" -> "CÁSSIA")
    item = {"major": 5, "minor": 75, "time": "2026-09-01T10:00:00-04:00",
            "name": "CÁSSIA PAOLA", "employeeNoString": "1", "serialNo": 1,
            "currentVerifyMode": "face"}
    ev = parse_acs_search_item(item, device_serial=SERIAL, device_id="d1")
    assert ev.person_name == "CÁSSIA PAOLA"


def test_loads_hik_utf8_names():
    from hikvision_access.util import loads_hik
    body = b'{"UserInfo":[{"name":"C\xc3\x81SSIA PAOLA"}]}'
    assert loads_hik(body)["UserInfo"][0]["name"] == "CÁSSIA PAOLA"
