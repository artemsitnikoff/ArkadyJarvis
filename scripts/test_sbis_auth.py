#!/usr/bin/env python3
"""Discovery: try Saby (SBIS) interactive login + company info lookup.

Pass credentials via env vars:
    SBIS_LOGIN=...
    SBIS_PASSWORD=...
    [SBIS_ACCOUNT=...]   # optional, only if you have multiple accounts

Run:
    docker compose exec -e SBIS_LOGIN=... -e SBIS_PASSWORD=... bot \
      python scripts/test_sbis_auth.py 7736050003
"""
import asyncio
import json
import os
import sys

import httpx

AUTH_URL = "https://online.sbis.ru/auth/service/"
RPC_URL = "https://online.sbis.ru/service/"


async def authenticate(http: httpx.AsyncClient, login: str, password: str, account: str | None) -> str:
    """Returns sid on success."""
    params: dict = {"Логин": login, "Пароль": password}
    if account:
        params["НомерАккаунта"] = account
    payload = {
        "jsonrpc": "2.0",
        "protocol": 6,
        "method": "СБИС.Аутентифицировать",
        "params": params,
        "id": 0,
    }
    print(f"\n→ POST {AUTH_URL}")
    print(f"  method: СБИС.Аутентифицировать")
    r = await http.post(AUTH_URL, json=payload, headers={"Content-Type": "application/json-rpc; charset=utf-8"})
    print(f"  status={r.status_code}  bytes={len(r.content)}")
    print(f"  raw response: {r.text[:500]}")
    r.raise_for_status()
    data = r.json()
    if data.get("error"):
        sys.exit(f"❌ Auth failed: {json.dumps(data['error'], ensure_ascii=False)}")
    sid = data.get("result")
    if not sid:
        sys.exit(f"❌ No result/sid in response: {data}")
    print(f"  ✅ sid = {sid[:20]}…")
    return sid


async def call_rpc(http: httpx.AsyncClient, sid: str, method: str, params: dict) -> dict:
    payload = {
        "jsonrpc": "2.0",
        "protocol": 6,
        "method": method,
        "params": params,
        "id": 0,
    }
    print(f"\n→ POST {RPC_URL}")
    print(f"  method: {method}")
    print(f"  params: {json.dumps(params, ensure_ascii=False)[:200]}")
    r = await http.post(
        RPC_URL,
        json=payload,
        headers={
            "Content-Type": "application/json-rpc; charset=utf-8",
            "X-SBISSessionID": sid,
        },
    )
    print(f"  status={r.status_code}  bytes={len(r.content)}")
    try:
        data = r.json()
    except Exception:
        print(f"  raw: {r.text[:500]!r}")
        return {}
    if data.get("error"):
        print(f"  ❌ error: {json.dumps(data['error'], ensure_ascii=False)[:300]}")
        return {}
    return data.get("result") or {}


async def main(inn: str) -> None:
    login = os.environ.get("SBIS_LOGIN", "").strip()
    password = os.environ.get("SBIS_PASSWORD", "").strip()
    account = os.environ.get("SBIS_ACCOUNT", "").strip() or None
    if not (login and password):
        sys.exit("Set SBIS_LOGIN and SBIS_PASSWORD env vars")

    async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
        sid = await authenticate(http, login, password, account)

        # 1. Basic company info (Электронный документооборот)
        result = await call_rpc(http, sid, "СБИС.ИнформацияОКонтрагенте", {"ИНН": inn})
        print("\n=== СБИС.ИнформацияОКонтрагенте ===")
        print(json.dumps(result, ensure_ascii=False, indent=2)[:2000])

        # 2. Try VOK basic requisites (might fail without license)
        try:
            r = await http.get(
                f"https://api.sbis.ru/vok/req?inn={inn}",
                headers={"X-SBISSessionID": sid, "X-SBISAccessToken": sid},
            )
            print(f"\n=== GET /vok/req?inn={inn} ===")
            print(f"  status={r.status_code}")
            print(f"  body: {r.text[:1000]!r}")
        except Exception as e:
            print(f"  VOK error: {e}")

        # 3. Demo VOK (works without auth)
        r = await http.get(f"https://api.sbis.ru/vok-demo/req?inn={inn}")
        print(f"\n=== GET /vok-demo/req?inn={inn} ===")
        print(f"  status={r.status_code}")
        print(f"  body: {r.text[:1000]!r}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/test_sbis_auth.py <inn>")
    asyncio.run(main(sys.argv[1]))
