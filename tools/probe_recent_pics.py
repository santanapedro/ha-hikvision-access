#!/usr/bin/env python3
"""Read-only: does AcsEvent search carry pictureURL for the most recent events?

One search request against the real terminal (device.local.json). Prints, per
InfoList item, serial / major.minor / name / whether pictureURL is present, and
tries to download the first pictureURL it finds.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types
import uuid
from datetime import datetime, timedelta

import aiohttp

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

_PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "hikvision_access"
_mod = types.ModuleType("hikvision_access")
_mod.__path__ = [str(_PKG)]
sys.modules["hikvision_access"] = _mod

from hikvision_access.api import HikvisionISAPIClient  # noqa: E402

CONF = json.loads((pathlib.Path(__file__).parent / "device.local.json").read_text())

BEGIN_SERIAL = int(sys.argv[1]) if len(sys.argv) > 1 else 19780


async def main() -> None:
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        client = HikvisionISAPIClient(
            session, CONF["host"], CONF.get("port") or 443,
            CONF["username"], CONF["password"],
            use_https=CONF.get("use_https", True),
        )
        now = datetime.now().astimezone().replace(microsecond=0)
        page = await client.async_search_acs_events(
            str(uuid.uuid4()),
            (now - timedelta(days=7)).isoformat(),
            (now + timedelta(minutes=1)).isoformat(),
            begin_serial_no=BEGIN_SERIAL,
            max_results=30,
        )
        infos = page.get("InfoList", [])
        print(f"beginSerialNo={BEGIN_SERIAL}  totalMatches={page.get('totalMatches')} "
              f"status={page.get('responseStatusStrg')}  page={len(infos)}")
        first_url = None
        for e in infos:
            url = e.get("pictureURL")
            if url and first_url is None:
                first_url = url
            print(f"  serial={e.get('serialNo')} {e.get('major')}.{e.get('minor')} "
                  f"name={e.get('name')!r} pic={'YES' if url else 'no'} "
                  f"time={e.get('time')}")
        if first_url:
            print(f"\nfirst pictureURL: {first_url}")
            try:
                blob = await client.async_get_bytes(first_url)
                print(f"download: {len(blob)} bytes jpeg={blob[:2] == bytes.fromhex('ffd8')}")
            except Exception as err:  # noqa: BLE001
                print(f"download FAILED: {err!r}")
        else:
            print("\nNO pictureURL on any recent item")


if __name__ == "__main__":
    asyncio.run(main())
