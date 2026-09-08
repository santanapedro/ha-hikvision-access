#!/usr/bin/env python3
"""Exercise custom_components/hikvision_access/api.py against the real terminal,
outside Home Assistant. Read-only (no door command).

    pip install aiohttp
    python tools/test_api.py
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

# register the component dir as a package WITHOUT running its HA __init__.py
_PKG = pathlib.Path(__file__).parents[1] / "custom_components" / "hikvision_access"
_mod = types.ModuleType("hikvision_access")
_mod.__path__ = [str(_PKG)]
sys.modules["hikvision_access"] = _mod

from hikvision_access import capabilities as caps_mod  # noqa: E402
from hikvision_access.api import HikvisionISAPIClient  # noqa: E402

CONF = json.loads((pathlib.Path(__file__).parent / "device.local.json").read_text())


async def main() -> None:
    connector = aiohttp.TCPConnector(ssl=False)
    async with aiohttp.ClientSession(connector=connector) as session:
        client = HikvisionISAPIClient(
            session,
            CONF["host"],
            CONF.get("port") or 443,
            CONF["username"],
            CONF["password"],
            use_https=CONF.get("use_https", True),
        )

        info = await client.async_get_device_info()
        print(f"device : {info.model}  fw {info.firmware}  "
              f"type={info.device_type}/{info.sub_device_type}  locks={info.electro_lock_num}")
        print(f"time   : {await client.async_get_time()}")

        caps = await caps_mod.async_discover(client)
        print(f"caps   : {caps}")

        now = datetime.now().astimezone().replace(microsecond=0)
        page = await client.async_search_acs_events(
            str(uuid.uuid4()),
            (now - timedelta(days=7)).isoformat(),
            now.isoformat(),
        )
        infos = page.get("InfoList", [])
        print(f"events : total={page.get('totalMatches')} page={len(infos)}")
        pic_url = None
        for e in infos:
            if e.get("pictureURL"):
                pic_url = e["pictureURL"]
                print(f"  sample granted: {e.get('name')!r} "
                      f"{e.get('major')}.{e.get('minor')} {e.get('time')}")
                break
        if pic_url:
            blob = await client.async_get_bytes(pic_url)
            print(f"picture: {len(blob)} bytes, jpeg={blob[:2] == bytes.fromhex('ffd8')}")


if __name__ == "__main__":
    asyncio.run(main())
