"""Digest computation and payload sniffing in api.py (no network)."""

import hashlib

import pytest
from hikvision_access.api import HikvisionISAPIClient, _Digest, _xml_to_dict
from hikvision_access.exceptions import HikvisionLockoutError, HikvisionProtocolError

CHALLENGE = (
    'Digest qop="auth", realm="DS-6E392E44", '
    'nonce="NjY1NzE5OGYyNmUzMDRhYWQwZTRkNDhjY2I5NTE4NmQ=", '
    'stale="false", opaque="", domain="::"'
)


def test_digest_matches_reference_rfc2617():
    d = _Digest("admin", "secret")
    d.load_challenge(CHALLENGE)
    header = d.header("GET", "/ISAPI/System/deviceInfo")

    fields = dict(
        part.strip().split("=", 1) for part in header[len("Digest ") :].split(", ")
    )
    nc = fields["nc"]
    cnonce = fields["cnonce"].strip('"')
    ha1 = hashlib.md5(b"admin:DS-6E392E44:secret").hexdigest()
    ha2 = hashlib.md5(b"GET:/ISAPI/System/deviceInfo").hexdigest()
    expected = hashlib.md5(
        f"{ha1}:{d.nonce}:{nc}:{cnonce}:auth:{ha2}".encode()
    ).hexdigest()
    assert fields["response"].strip('"') == expected


def test_digest_nonce_count_increments():
    d = _Digest("u", "p")
    d.load_challenge(CHALLENGE)
    first = dict(p.split("=", 1) for p in d.header("GET", "/a")[7:].split(", "))["nc"]
    second = dict(p.split("=", 1) for p in d.header("GET", "/a")[7:].split(", "))["nc"]
    assert first == "00000001"
    assert second == "00000002"


def test_digest_nc_resets_on_new_nonce():
    """A fresh nonce must restart nc at 1 (the terminal rejects otherwise)."""
    d = _Digest("u", "p")
    d.load_challenge(CHALLENGE)
    d.header("GET", "/a")
    d.header("GET", "/a")  # nc now at 2
    other = CHALLENGE.replace("NjY1", "ZZZ9")  # different nonce
    d.load_challenge(other)
    nc = dict(p.split("=", 1) for p in d.header("GET", "/a")[7:].split(", "))["nc"]
    assert nc == "00000001"


def test_digest_same_nonce_keeps_counting():
    d = _Digest("u", "p")
    d.load_challenge(CHALLENGE)
    d.header("GET", "/a")
    d.load_challenge(CHALLENGE)  # same nonce again
    nc = dict(p.split("=", 1) for p in d.header("GET", "/a")[7:].split(", "))["nc"]
    assert nc == "00000002"


def test_lockout_detection():
    body = (
        "<userCheck><statusValue>401</statusValue>"
        "<lockStatus>lock</lockStatus><unlockTime>111</unlockTime></userCheck>"
    )
    with pytest.raises(HikvisionLockoutError) as exc:
        HikvisionISAPIClient._raise_if_locked(body)
    assert exc.value.unlock_seconds == 111


def test_no_lockout_on_normal_body():
    HikvisionISAPIClient._raise_if_locked("<DeviceInfo><model>X</model></DeviceInfo>")


def test_xml_to_dict_strips_namespace():
    xml = (
        '<DeviceInfo version="2.0" xmlns="http://www.isapi.org/ver20/XMLSchema">'
        "<model>DS-K1T342MWX</model><serialNumber>ABC123</serialNumber>"
        "<firmwareVersion>V3.16.1</firmwareVersion></DeviceInfo>"
    )
    d = _xml_to_dict(xml)
    assert d["model"] == "DS-K1T342MWX"
    assert d["serialNumber"] == "ABC123"


def test_xml_to_dict_rejects_garbage():
    with pytest.raises(HikvisionProtocolError):
        _xml_to_dict("not xml <<<")
