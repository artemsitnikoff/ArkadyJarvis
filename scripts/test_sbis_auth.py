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
    import base64
    body = {"jsonrpc": "2.0", "protocol": 6, "method": method, "params": params, "id": 0}
    # SBIS uses base64-encoded UTF-8 method name in X-OriginalMethodName for non-ASCII.
    # X-CalledMethod can hold the ASCII fallback or the base64 too.
    method_b64 = base64.b64encode(method.encode("utf-8")).decode("ascii")
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "X-OriginalMethodName": method_b64,
    }
    # Only add X-CalledMethod if method is ASCII
    try:
        method.encode("ascii")
        headers["X-CalledMethod"] = method
    except UnicodeEncodeError:
        # For Cyrillic methods, use base64 version
        headers["X-CalledMethod"] = method_b64
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
            # 1. Try Контрагент.НайтиПоРеквизитам — found it needs ДопПоля
            ("https://online.sbis.ru/service/", "Контрагент.НайтиПоРеквизитам",
             {"Реквизиты": inn, "ДопПоля": []}),
            ("https://online.sbis.ru/service/", "Контрагент.НайтиПоРеквизитам",
             {"Реквизиты": inn, "ДопПоля": ["Адрес", "Руководитель", "Телефон", "Email", "ОсновнойОКВЭД"]}),
            ("https://online.sbis.ru/service/", "Контрагент.НайтиПоРеквизитам",
             {"Реквизиты": inn, "ДопПоля": ["*"]}),
            # 2. Other plausible search methods
            ("https://online.sbis.ru/service/", "Контрагент.Найти",
             {"Реквизиты": inn, "ДопПоля": []}),
            ("https://online.sbis.ru/service/", "ВнешнееЛицо.НайтиПоРеквизитам",
             {"Реквизиты": inn, "ДопПоля": []}),
        ]
        successes = []
        for url, method, params in attempts:
            res = await call_rpc(http, url, method, params, cookies)
            if res:
                successes.append((method, params, res))
                print(f"  ✅ SUCCESS — preview:")
                print(json.dumps(res, ensure_ascii=False, indent=2)[:1500])
                print()
        print(f"\n=== Summary: {len(successes)} succeeded out of {len(attempts)} ===")
        for method, params, _ in successes:
            print(f"  ✅ {method}  with params: {json.dumps(params, ensure_ascii=False)[:100]}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/test_sbis_auth.py <inn>")
    asyncio.run(main(sys.argv[1]))
