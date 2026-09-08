#!/usr/bin/env python3
"""Paginate the terminal's access-event log and tally (major, minor) pairs, so the
event mapper is built from what this device actually emits. Read-only.

    python event_histogram.py [--days 120] [--max-pages 60]
"""
from __future__ import annotations
import argparse, json, sys, uuid
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import requests
from requests.auth import HTTPDigestAuth
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HERE = Path(__file__).parent
conf = json.loads((HERE / "device.local.json").read_text(encoding="utf-8"))
BASE = f"https://{conf['host']}"
AUTH = HTTPDigestAuth(conf["username"], conf["password"])

ap = argparse.ArgumentParser()
ap.add_argument("--days", type=int, default=120)
ap.add_argument("--max-pages", type=int, default=80)
args = ap.parse_args()

now = datetime.now().astimezone()
start = (now - timedelta(days=args.days)).replace(microsecond=0).isoformat()
end = now.replace(microsecond=0).isoformat()

pairs: Counter = Counter()
samples: dict[str, dict] = {}
sid = str(uuid.uuid4())
pos = 0
total = None
denied_examples = []

for page in range(args.max_pages):
    body = {"AcsEventCond": {"searchID": sid, "searchResultPosition": pos,
                             "maxResults": 30, "major": 0, "minor": 0,
                             "startTime": start, "endTime": end}}
    r = requests.post(f"{BASE}/ISAPI/AccessControl/AcsEvent?format=json",
                      json=body, auth=AUTH, verify=False, timeout=20)
    if r.status_code != 200:
        print(f"page {page}: HTTP {r.status_code} {r.text[:200]}"); break
    data = r.json().get("AcsEvent", {})
    total = data.get("totalMatches", total)
    infos = data.get("InfoList", []) or []
    for e in infos:
        key = f'{e.get("major")}.{e.get("minor")}'
        pairs[key] += 1
        if key not in samples:
            samples[key] = e
    pos += len(infos)
    if data.get("responseStatusStrg") != "MORE" or not infos:
        break

print(f"total in window (~{args.days}d): {total}   scanned: {pos}\n")
print(f"{'major.minor':>12}  {'count':>6}  example fields")
for key, cnt in pairs.most_common():
    ex = samples[key]
    fields = [k for k in ("name", "employeeNoString", "cardNo", "doorNo",
                          "currentVerifyMode", "pictureURL", "userType",
                          "remoteHostAddr", "statusValue") if k in ex]
    print(f"{key:>12}  {cnt:>6}  {', '.join(fields)}")

out = HERE / "output" / f"histogram-{now.strftime('%Y%m%d-%H%M%S')}.json"
out.write_text(json.dumps({"total": total, "scanned": pos,
                           "pairs": dict(pairs), "samples": samples}, indent=2,
                          default=str), encoding="utf-8")
print(f"\nsaved {out}")
