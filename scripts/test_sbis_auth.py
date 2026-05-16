#!/usr/bin/env python3
"""Discovery for Saby/SBIS API via cookie-based session auth.

Paste the ENTIRE Cookie header from browser DevTools as SBIS_COOKIE
env var. Run with a test INN as argument:

    docker compose exec \
      -e SBIS_COOKIE="sid=...; s3sid-online-daab=...; ..." \
      bot python scripts/test_sbis_auth.py 7736050003
"""
import asyncio
import json
import os
import sys
from http.cookies import SimpleCookie

import httpx


def parse_cookie_str(cookie_str: str) -> dict[str, str]:
    """Parse a raw 'Cookie:' header value into a name→value dict."""
    sc = SimpleCookie()
    sc.load(cookie_str)
    return {k: v.value for k, v in sc.items()}


async def call_rpc(
    http: httpx.AsyncClient,
    endpoint: str,
    method: str,
    params: dict,
    cookies: dict,
) -> dict | None:
    body = {"jsonrpc": "2.0", "protocol": 6, "method": method, "params": params, "id": 0}
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-CalledMethod": method,
    }
    print(f"\n→ POST {endpoint}  method={method}")
    print(f"  params: {json.dumps(params, ensure_ascii=False)[:200]}")
    try:
        r = await http.post(endpoint, json=body, headers=headers, cookies=cookies)
    except Exception as e:
        print(f"  exception: {e}")
        return None
    print(f"  status={r.status_code}  bytes={len(r.content)}")
    if r.status_code >= 400:
        print(f"  err body: {r.text[:500]!r}")
        return None
    try:
        data = r.json()
    except Exception:
        print(f"  not json — body: {r.text[:500]!r}")
        return None
    if data.get("error"):
        print(f"  ❌ error: {json.dumps(data['error'], ensure_ascii=False)[:300]}")
        return None
    return data


async def main(inn: str) -> None:
    cookie_str = os.environ.get("SBIS_COOKIE", "").strip()
    if not cookie_str:
        sys.exit("Set SBIS_COOKIE env var with raw Cookie header from browser")
    cookies = parse_cookie_str(cookie_str)
    print(f"Parsed {len(cookies)} cookies. Key ones present:")
    for k in ("sid", "s3sid-online-daab", "s3tok-daab", "CpsUserId", "cloud_device_id"):
        v = cookies.get(k)
        print(f"  {k}: {v[:40] + '…' if v and len(v) > 40 else v!r}")

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        # Try several method/endpoint combinations to find what works.
        # СБИС.ИнформацияОКонтрагенте is the basic EDO method for contractor info.
        attempts = [
            ("https://online.sbis.ru/service/", "СБИС.ИнформацияОКонтрагенте", {"ИНН": inn}),
            ("https://online.sbis.ru/service/", "Контрагент.СводкаПоКонтрагенту", {"ИНН": inn}),
            ("https://online.sbis.ru/service/", "Контрагент.Найти", {"Запрос": inn, "Страница": 0, "РазмерСтраницы": 10}),
            ("https://profile.saby.ru/service/", "СБИС.ИнформацияОКонтрагенте", {"ИНН": inn}),
        ]
        for url, method, params in attempts:
            res = await call_rpc(http, url, method, params, cookies)
            if res:
                print(f"\n  ✅ result preview:")
                print(json.dumps(res, ensure_ascii=False, indent=2)[:2500])
                print(f"\n  (full size: {len(json.dumps(res, ensure_ascii=False))} chars)")
                break


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/test_sbis_auth.py <inn>")
    asyncio.run(main(sys.argv[1]))
