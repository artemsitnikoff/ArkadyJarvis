#!/usr/bin/env python3
"""Try SBIS partner API (spp-rest-api) using s3tok-daab as X-SBISAccessToken."""
import asyncio
import json
import os
import sys
from http.cookies import SimpleCookie

import httpx


async def main(inn: str) -> None:
    cookie_str = os.environ.get("SBIS_COOKIE", "").strip()
    if not cookie_str:
        sys.exit("Set SBIS_COOKIE env var")
    sc = SimpleCookie()
    sc.load(cookie_str)
    cookies = {k: v.value for k, v in sc.items()}

    access_token = cookies.get("s3tok-daab")
    sid = cookies.get("sid") or cookies.get("s3sid-online-daab")
    print(f"access_token: {access_token[:30] if access_token else 'None'}…")
    print(f"sid: {sid}")

    attempts = [
        # spp-rest-api
        ("https://api.saby.ru/spp-rest-api/service/", "Contractor.Find",
         {"requisites": inn, "page": 0, "size": 10}, "header_token"),
        ("https://api.sbis.ru/spp-rest-api/service/", "Contractor.Find",
         {"requisites": inn, "page": 0, "size": 10}, "header_token"),
        # VOK
        ("https://api.sbis.ru/vok/req?inn=" + inn, None, None, "header_token_get"),
        ("https://api.saby.ru/vok/req?inn=" + inn, None, None, "header_token_get"),
        # SBIS Profile (online check)
        ("https://saby.ru/help/integration/api/auth", None, None, "header_token_get"),  # control
    ]

    async with httpx.AsyncClient(timeout=30) as http:
        for url, method, params, auth_mode in attempts:
            print(f"\n→ {url}")
            print(f"  method={method}  auth={auth_mode}")
            headers = {
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json",
            }
            if auth_mode == "header_token":
                headers["X-SBISAccessToken"] = access_token
                headers["X-SBISSessionID"] = sid or ""
                body = {"jsonrpc": "2.0", "protocol": 6, "method": method, "params": params, "id": 0}
                try:
                    r = await http.post(url, json=body, headers=headers, cookies=cookies)
                except Exception as e:
                    print(f"  exception: {e}")
                    continue
            else:
                headers["X-SBISAccessToken"] = access_token
                try:
                    r = await http.get(url, headers=headers, cookies=cookies)
                except Exception as e:
                    print(f"  exception: {e}")
                    continue

            print(f"  status={r.status_code}  bytes={len(r.content)}")
            body = r.text[:500]
            print(f"  body: {body!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: test_sbis_partner.py <inn>")
    asyncio.run(main(sys.argv[1]))
