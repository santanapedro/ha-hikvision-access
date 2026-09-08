#!/usr/bin/env python3
"""End-to-end pipeline check against the real terminal, outside Home Assistant.

Runs the reconciliation path for real: AcsEvent search -> parse -> SQLite store
-> image download. Then runs it again to prove dedupe. Read-only on the device.

    python tools/test_pipeline.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import tempfile
import types
import uuid
from datetime import datetime, timedelta

import aiohttp

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "hikvision_access"
_m = types.ModuleType("hikvision_access")
_m.__path__ = [str(_PKG)]
sys.modules["hikvision_access"] = _m

from hikvision_access.api import HikvisionISAPIClient  # noqa: E402
from hikvision_access.event_parser import parse_acs_search_item  # noqa: E402
from hikvision_access.storage import EventStore  # noqa: E402

CONF = json.loads((pathlib.Path(__file__).parent / "device.local.json").read_text())


async def reconcile(client, store, serial, *, days=30):
    now = datetime.now().astimezone().replace(microsecond=0)
    start = (now - timedelta(days=days)).isoformat()
    end = (now + timedelta(minutes=1)).isoformat()
    sid = str(uuid.uuid4())
    pos = 0
    new = 0
    total_seen = 0
    max_serial = await store.async_max_serial(serial)
    begin = None if max_serial is None else max(1, max_serial - 4)
    for _ in range(300):
        page = await client.async_search_acs_events(
            sid, start, end, position=pos, max_results=30, begin_serial_no=begin
        )
        infos = page.get("InfoList") or []
        if not infos:
            break
        for item in infos:
            ev = parse_acs_search_item(
                item, device_serial=serial, device_id=serial, door_name="Porta"
            )
            if await store.async_insert_event(ev):
                new += 1
        total_seen += len(infos)
        pos += len(infos)
        if page.get("responseStatusStrg") != "MORE":
            break
    return new, total_seen, page.get("totalMatches")


async def main():
    connector = aiohttp.TCPConnector(ssl=False)
    tmp = pathlib.Path(tempfile.mkdtemp()) / "pipeline.db"
    async with aiohttp.ClientSession(connector=connector) as session:
        client = HikvisionISAPIClient(
            session, CONF["host"], CONF.get("port") or 443,
            CONF["username"], CONF["password"], use_https=CONF.get("use_https", True),
        )
        info = await client.async_get_device_info()
        serial = info.serial_number
        print(f"device: {info.model}  serial={serial[:12]}...")

        store = EventStore(tmp)
        await store.async_open()
        await store.async_upsert_device(
            {"id": serial, "name": info.model, "model": info.model,
             "serial_number": serial, "firmware": info.firmware, "mac": info.mac}
        )

        new1, seen1, total = await reconcile(client, store, serial, days=30)
        print(f"run 1: totalMatches={total}  scanned={seen1}  inserted={new1}")

        new2, seen2, _ = await reconcile(client, store, serial, days=30)
        print(f"run 2: scanned={seen2}  inserted={new2}  (dedupe -> expect 0)")

        rows = await store.async_query_events(device_id=serial, limit=500)
        granted = [r for r in rows if r["access_result"] == "granted"]
        withpic = [r for r in granted if r["event_picture_url"]]
        print(f"stored: {len(rows)} events, {len(granted)} granted, "
              f"{len(withpic)} with pictureURL")
        print(f"max serial tracked: {await store.async_max_serial(serial)}")

        if withpic:
            sample = withpic[0]
            print(f"sample granted: {sample['person_name']!r} "
                  f"{sample['authentication_method']} @ {sample['timestamp']}")
            blob = await client.async_get_bytes(sample["event_picture_url"])
            print(f"  image: {len(blob)} bytes  jpeg={blob[:2] == bytes.fromhex('ffd8')}")

        await store.async_close()
        ok = new2 == 0 and new1 > 0
        print("\nPIPELINE OK" if ok else "\nPIPELINE: check results above")


if __name__ == "__main__":
    asyncio.run(main())
