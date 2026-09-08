#!/usr/bin/env python3
"""Discovery tool for Hikvision access-control terminals (DS-K1T3xx MinMoe family).

Read-only probe. It NEVER opens a door, writes users, or changes config.
It only performs GET requests plus the documented POST *search* endpoints, and
holds the alertStream connection open to observe real events.

Output goes to  tools/output/discovery-<timestamp>/  :
    summary.json        - high level: model, firmware, endpoint support matrix
    capabilities.json   - the ds-k1t3xx-capabilities.json described in the spec
    raw/*.txt           - full (redacted) responses for every probed endpoint
    fixtures/*          - redacted samples to seed the parser / tests

Usage:
    python discovery.py --host 192.168.1.50 --username integration_ha --password 'secret'
    python discovery.py --host 192.168.1.50 -u ha -p secret --http --duration 40

Or drop the connection details in  tools/device.local.json  (git-ignored):
    { "host": "192.168.1.50", "username": "ha", "password": "secret",
      "use_https": true, "verify_ssl": false, "port": null }
and just run:  python discovery.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import requests
    from requests.auth import HTTPDigestAuth
    import urllib3
except ImportError:
    sys.exit("Missing dependency. Run:  pip install requests")

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HERE = Path(__file__).parent
LOCAL_CONF = HERE / "device.local.json"

# ---------------------------------------------------------------------------
# Redaction: keep the shapes, drop the PII / secrets.
# ---------------------------------------------------------------------------

_REDACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'("serialNumber"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED_SERIAL\g<2>"),
    (re.compile(r'(<serialNumber>)[^<]*(</serialNumber>)', re.I), r"\g<1>REDACTED_SERIAL\g<2>"),
    (re.compile(r'("macAddress"\s*:\s*")[^"]*(")', re.I), r"\g<1>AA:BB:CC:DD:EE:FF\g<2>"),
    (re.compile(r'(<macAddress>)[^<]*(</macAddress>)', re.I), r"\g<1>AA:BB:CC:DD:EE:FF\g<2>"),
    (re.compile(r'("ipAddress"\s*:\s*")[^"]*(")', re.I), r"\g<1>192.0.2.10\g<2>"),
    (re.compile(r'(<ipAddress>)[^<]*(</ipAddress>)', re.I), r"\g<1>192.0.2.10\g<2>"),
    (re.compile(r'("ipv6Address"\s*:\s*")[^"]*(")', re.I), r"\g<1>::\g<2>"),
    (re.compile(r'("name"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED_NAME\g<2>"),
    (re.compile(r'(<name>)[^<]*(</name>)', re.I), r"\g<1>REDACTED_NAME\g<2>"),
    (re.compile(r'("cardNo"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED_CARD\g<2>"),
    (re.compile(r'(<cardNo>)[^<]*(</cardNo>)', re.I), r"\g<1>REDACTED_CARD\g<2>"),
    (re.compile(r'("employeeNoString"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED_EMP\g<2>"),
    (re.compile(r'("employeeNo"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED_EMP\g<2>"),
    (re.compile(r'("telephone"\s*:\s*")[^"]*(")', re.I), r"\g<1>\g<2>"),
    (re.compile(r'("Authorization"\s*:\s*")[^"]*(")', re.I), r"\g<1>REDACTED\g<2>"),
]


def redact(text: str, host: str) -> str:
    if not text:
        return text
    out = text.replace(host, "DEVICE_HOST")
    for pat, repl in _REDACT_PATTERNS:
        out = pat.sub(repl, out)
    return out


# ---------------------------------------------------------------------------
# Probe list. (endpoint, method, json_body_or_None, note)
# GET-only + documented *search* POSTs. Nothing here mutates the device.
# ---------------------------------------------------------------------------

def _search_id() -> str:
    return str(uuid.uuid4())


def build_probes(now: datetime) -> list[dict]:
    start = (now - timedelta(days=7)).isoformat(timespec="seconds")
    end = now.isoformat(timespec="seconds")
    return [
        # --- identity / system ---
        {"key": "device_info", "url": "/ISAPI/System/deviceInfo", "method": "GET"},
        {"key": "system_capabilities", "url": "/ISAPI/System/capabilities", "method": "GET"},
        {"key": "system_status", "url": "/ISAPI/System/status", "method": "GET"},
        {"key": "system_time", "url": "/ISAPI/System/time", "method": "GET"},
        {"key": "network_interfaces", "url": "/ISAPI/System/Network/interfaces", "method": "GET"},
        {"key": "security_capabilities", "url": "/ISAPI/Security/capabilities", "method": "GET"},
        # --- event subsystem ---
        {"key": "event_capabilities", "url": "/ISAPI/Event/capabilities", "method": "GET"},
        {"key": "event_triggers", "url": "/ISAPI/Event/triggers", "method": "GET"},
        {"key": "event_notification_httphosts", "url": "/ISAPI/Event/notification/httpHosts", "method": "GET"},
        # --- access control ---
        {"key": "acs_capabilities", "url": "/ISAPI/AccessControl/capabilities?format=json", "method": "GET"},
        {"key": "acs_event_capabilities", "url": "/ISAPI/AccessControl/AcsEvent/capabilities?format=json", "method": "GET"},
        {"key": "acs_workstatus", "url": "/ISAPI/AccessControl/AcsWorkStatus?format=json", "method": "GET"},
        {"key": "door_status", "url": "/ISAPI/AccessControl/Door/param/1?format=json", "method": "GET"},
        {"key": "remotecontrol_door_caps", "url": "/ISAPI/AccessControl/RemoteControl/door/capabilities?format=json", "method": "GET"},
        {"key": "capture_face_data_caps", "url": "/ISAPI/AccessControl/CaptureFaceData/capabilities?format=json", "method": "GET"},
        {"key": "face_recognize_mode", "url": "/ISAPI/AccessControl/FaceRecognizeMode?format=json", "method": "GET"},
        # --- users / cards ---
        {"key": "userinfo_capabilities", "url": "/ISAPI/AccessControl/UserInfo/capabilities?format=json", "method": "GET"},
        {"key": "userinfo_count", "url": "/ISAPI/AccessControl/UserInfo/Count?format=json", "method": "GET"},
        {"key": "cardinfo_count", "url": "/ISAPI/AccessControl/CardInfo/Count?format=json", "method": "GET"},
        {"key": "facedata_caps", "url": "/ISAPI/Intelligent/FDLib/capabilities?format=json", "method": "GET"},
        # --- media ---
        {"key": "streaming_channels", "url": "/ISAPI/Streaming/channels", "method": "GET"},
        {"key": "video_inputs", "url": "/ISAPI/System/Video/inputs/channels", "method": "GET"},
        {"key": "contentmgmt_caps", "url": "/ISAPI/ContentMgmt/capabilities", "method": "GET"},
        # --- documented search POSTs (read-only) ---
        {
            "key": "acs_event_search",
            "url": "/ISAPI/AccessControl/AcsEvent?format=json",
            "method": "POST",
            "json": {
                "AcsEventCond": {
                    "searchID": _search_id(),
                    "searchResultPosition": 0,
                    "maxResults": 20,
                    "major": 0,
                    "minor": 0,
                    "startTime": start,
                    "endTime": end,
                }
            },
        },
        {
            "key": "userinfo_search",
            "url": "/ISAPI/AccessControl/UserInfo/Search?format=json",
            "method": "POST",
            "json": {
                "UserInfoSearchCond": {
                    "searchID": _search_id(),
                    "searchResultPosition": 0,
                    "maxResults": 5,
                }
            },
        },
    ]


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class Client:
    def __init__(self, host, username, password, use_https, verify_ssl, port):
        scheme = "https" if use_https else "http"
        if port is None:
            port = 443 if use_https else 80
        self.base = f"{scheme}://{host}:{port}"
        self.host = host
        self.session = requests.Session()
        self.session.verify = verify_ssl
        self.session.auth = HTTPDigestAuth(username, password)

    def request(self, method, path, json_body=None, timeout=15):
        url = self.base + path
        try:
            # Digest only. No basic fallback — extra 401s trip the terminal's
            # "illegal login" lockout (returns <userCheck><lockStatus>lock).
            return self.session.request(method, url, json=json_body, timeout=timeout)
        except requests.exceptions.SSLError as e:
            return _FakeResp(-1, f"SSLError: {e}")
        except requests.exceptions.ConnectTimeout as e:
            return _FakeResp(-2, f"ConnectTimeout: {e}")
        except requests.exceptions.ConnectionError as e:
            return _FakeResp(-3, f"ConnectionError: {e}")
        except requests.exceptions.RequestException as e:
            return _FakeResp(-9, f"RequestException: {e}")

    def stream_alert(self, seconds, byte_cap=400_000):
        """Hold /ISAPI/Event/notification/alertStream open and capture what arrives."""
        url = self.base + "/ISAPI/Event/notification/alertStream"
        info = {
            "endpoint": "/ISAPI/Event/notification/alertStream",
            "duration_s": seconds,
            "status_code": None,
            "content_type": None,
            "bytes_received": 0,
            "error": None,
            "sample": "",
        }
        try:
            r = self.session.get(url, stream=True, timeout=(10, seconds + 10))
            info["status_code"] = r.status_code
            info["content_type"] = r.headers.get("Content-Type")
            if r.status_code != 200:
                info["error"] = f"HTTP {r.status_code}"
                info["sample"] = r.text[:2000]
                return info
            buf = bytearray()
            deadline = time.time() + seconds
            for chunk in r.iter_content(chunk_size=1024):
                if chunk:
                    buf.extend(chunk)
                if len(buf) >= byte_cap or time.time() >= deadline:
                    break
            r.close()
            info["bytes_received"] = len(buf)
            info["sample"] = buf.decode("utf-8", errors="replace")[:20000]
        except requests.exceptions.RequestException as e:
            info["error"] = f"{type(e).__name__}: {e}"
        return info


class _FakeResp:
    def __init__(self, status_code, text):
        self.status_code = status_code
        self.text = text
        self.headers = {}
        self.content = text.encode()

    def json(self):
        raise ValueError("not json")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def load_config(args) -> dict:
    conf = {}
    if LOCAL_CONF.exists():
        conf = json.loads(LOCAL_CONF.read_text(encoding="utf-8"))
    if args.host:
        conf["host"] = args.host
    if args.username:
        conf["username"] = args.username
    if args.password:
        conf["password"] = args.password
    if args.http:
        conf["use_https"] = False
    if args.port:
        conf["port"] = args.port
    conf.setdefault("use_https", True)
    conf.setdefault("verify_ssl", False)
    conf.setdefault("port", None)
    missing = [k for k in ("host", "username", "password") if not conf.get(k)]
    if missing:
        sys.exit(
            f"Missing: {', '.join(missing)}.\n"
            f"Pass --host/--username/--password or create {LOCAL_CONF.name}."
        )
    return conf


def summarize_status(code: int) -> str:
    if code == 200:
        return "supported"
    if code in (403, 404, 501):
        return "unsupported"
    if code == 401:
        return "auth_error"
    if code and code < 0:
        return "transport_error"
    return f"http_{code}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host")
    ap.add_argument("-u", "--username")
    ap.add_argument("-p", "--password")
    ap.add_argument("--http", action="store_true", help="use plain HTTP instead of HTTPS")
    ap.add_argument("--port", type=int)
    ap.add_argument("--duration", type=int, default=30, help="seconds to hold alertStream open (default 30)")
    ap.add_argument("--skip-stream", action="store_true")
    args = ap.parse_args()

    conf = load_config(args)
    host = conf["host"]
    client = Client(host, conf["username"], conf["password"],
                    conf["use_https"], conf["verify_ssl"], conf["port"])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    outdir = HERE / "output" / f"discovery-{stamp}"
    (outdir / "raw").mkdir(parents=True, exist_ok=True)
    (outdir / "fixtures").mkdir(parents=True, exist_ok=True)

    print(f"Target : {client.base}")
    print(f"Output : {outdir}")
    print("-" * 60)

    # --- preflight: one auth check, bail out on lockout ---
    pre = client.request("GET", "/ISAPI/System/deviceInfo")
    pre_body = getattr(pre, "text", "") or ""
    if "lockStatus" in pre_body and "lock" in pre_body:
        m = re.search(r"<unlockTime>(\d+)</unlockTime>", pre_body)
        wait = m.group(1) if m else "?"
        sys.exit(
            f"Terminal has locked out logins (too many failed auth attempts).\n"
            f"unlockTime = {wait}s. Wait for it to clear, verify the password, "
            f"then re-run. Do NOT hammer it in the meantime."
        )
    if pre.status_code == 401:
        sys.exit(
            "Auth failed (HTTP 401) but no lockout flag. Check username/password "
            f"and that this account may use ISAPI.\nBody: {pre_body[:400]}"
        )
    if pre.status_code != 200:
        print(f"  warning: preflight deviceInfo returned HTTP {pre.status_code}")
    else:
        print("  preflight auth OK")

    now = datetime.now(timezone.utc).astimezone()
    probes = build_probes(now)
    matrix = {}

    for probe in probes:
        key, path, method = probe["key"], probe["url"], probe["method"]
        time.sleep(0.4)
        r = client.request(method, path, probe.get("json"))
        status = summarize_status(r.status_code)
        if "lockStatus" in (getattr(r, "text", "") or "") and status == "auth_error":
            sys.exit("Terminal locked out mid-run — stopping. Re-run after the unlock window.")
        ctype = r.headers.get("Content-Type", "")
        body = redact(getattr(r, "text", "") or "", host)
        matrix[key] = {
            "endpoint": path,
            "method": method,
            "status_code": r.status_code,
            "result": status,
            "content_type": ctype,
            "body_bytes": len(body),
        }
        raw = [f"# {method} {path}", f"# HTTP {r.status_code}  {ctype}", ""]
        if probe.get("json"):
            raw.append("## request body")
            raw.append(json.dumps(probe["json"], indent=2))
            raw.append("")
        raw.append("## response body")
        raw.append(body[:60000])
        (outdir / "raw" / f"{key}.txt").write_text("\n".join(raw), encoding="utf-8")

        if status == "supported" and body.strip():
            ext = "json" if "json" in ctype else ("xml" if "xml" in ctype else "txt")
            (outdir / "fixtures" / f"{key}.{ext}").write_text(body[:60000], encoding="utf-8")

        flag = {"supported": "OK ", "unsupported": "-- ", "auth_error": "401",
                "transport_error": "ERR"}.get(status, "???")
        print(f"  [{flag}] {method:4} {path}")

    # --- alertStream ---
    stream_info = None
    if not args.skip_stream:
        print("-" * 60)
        print(f"  Holding alertStream open for {args.duration}s "
              f"— trigger some accesses on the terminal now...")
        stream_info = client.stream_alert(args.duration)
        stream_info["sample"] = redact(stream_info.get("sample", ""), host)
        (outdir / "raw" / "alertStream.txt").write_text(
            f"# GET /ISAPI/Event/notification/alertStream\n"
            f"# HTTP {stream_info['status_code']}  {stream_info['content_type']}\n"
            f"# bytes: {stream_info['bytes_received']}  error: {stream_info['error']}\n\n"
            f"{stream_info['sample']}",
            encoding="utf-8",
        )
        if stream_info["bytes_received"]:
            (outdir / "fixtures" / "alertStream.sample.txt").write_text(
                stream_info["sample"], encoding="utf-8")
        print(f"  alertStream: HTTP {stream_info['status_code']}, "
              f"{stream_info['bytes_received']} bytes, "
              f"ct={stream_info['content_type']}")

    # --- capability doc (spec section 38) ---
    def sup(k):
        return matrix.get(k, {}).get("result") == "supported"

    capabilities_doc = {
        "generated": stamp,
        "target": client.base.replace(host, "DEVICE_HOST"),
        "endpoints": {
            "device_info": {"supported": sup("device_info")},
            "alert_stream": {
                "supported": bool(stream_info and stream_info["status_code"] == 200),
                "content_type": stream_info["content_type"] if stream_info else None,
                "received_events": bool(stream_info and stream_info["bytes_received"] > 0),
            },
            "acs_event_search": {"supported": sup("acs_event_search")},
            "user_search": {"supported": sup("userinfo_search")},
            "user_count": {"supported": sup("userinfo_count")},
            "remote_door": {"supported": sup("remotecontrol_door_caps"),
                            "note": "capabilities only probed; PUT not tested by discovery"},
            "door_status": {"supported": sup("door_status")},
            "event_picture": {"supported": None, "note": "determine from AcsEvent / alertStream fixture"},
            "acs_capabilities": {"supported": sup("acs_capabilities")},
            "event_capabilities": {"supported": sup("event_capabilities")},
        },
    }
    (outdir / "capabilities.json").write_text(
        json.dumps(capabilities_doc, indent=2), encoding="utf-8")

    summary = {
        "generated": stamp,
        "matrix": matrix,
        "alert_stream": stream_info,
        "next_steps": [
            "Inspect fixtures/acs_event_search.json for the real event field names "
            "(employeeNo, name, cardNo, doorNo, currentVerifyMode, pictureURL...).",
            "Inspect fixtures/alertStream.sample.txt for the realtime payload format "
            "(multipart boundary? XML? JSON? picture inline?).",
            "Compare major/minor codes between the two to build event_mapper.",
            "Check whether an event carries a picture URL or inline JPEG.",
        ],
    }
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print("-" * 60)
    print(f"Done. Review:\n  {outdir / 'summary.json'}\n  {outdir / 'capabilities.json'}\n  {outdir / 'fixtures'}/")


if __name__ == "__main__":
    main()
