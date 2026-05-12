#!/usr/bin/env python3
"""Test the Potok frontend communicate endpoint using DeviseTokenAuth tokens
captured from the browser DevTools.

Set env vars before running (or paste tokens below):
    POTOK_FRONTEND_ACCESS_TOKEN=...
    POTOK_FRONTEND_CLIENT=...
    POTOK_FRONTEND_UID=...

Run:
    docker compose exec -e POTOK_FRONTEND_ACCESS_TOKEN=... -e POTOK_FRONTEND_CLIENT=... -e POTOK_FRONTEND_UID=... bot python scripts/test_potok_communicate_frontend.py 1596058 58261774
"""
import asyncio
import json
import os
import sys

import httpx

# Test payload — captured from DevTools 2026-05-12.
# `channels` is the HH negotiation/dialog ID — varies per candidate.
# Pass it as the 3rd CLI arg.
def build_payload(channel_id: str, body: str) -> dict:
    return {
        "communication_envelopes": [
            {
                "provider": "headhunter",
                "channels": [channel_id],
                "message": {"body": body},
            }
        ]
    }


async def main(job_id: int, applicant_id: int, channel_id: str) -> None:
    access_token = os.environ.get("POTOK_FRONTEND_ACCESS_TOKEN", "").strip()
    client = os.environ.get("POTOK_FRONTEND_CLIENT", "").strip()
    uid = os.environ.get("POTOK_FRONTEND_UID", "").strip()

    if not (access_token and client and uid):
        sys.exit(
            "Set POTOK_FRONTEND_ACCESS_TOKEN, POTOK_FRONTEND_CLIENT, "
            "POTOK_FRONTEND_UID env vars (from browser DevTools)."
        )

    headers = {
        "access-token": access_token,
        "client": client,
        "uid": uid,
        "token-type": "Bearer",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": "https://app.potok.io",
        "Referer": f"https://app.potok.io/j/{job_id}/all/a/{applicant_id}/",
        "User-Agent": "Mozilla/5.0 (compatible; ArkadyJarvis/1.0)",
    }

    url = f"https://app.potok.io/client_api/jobs/{job_id}/{applicant_id}/communication/communicate.json"

    payload = build_payload(channel_id, "Тест отправки через API — пожалуйста, проигнорируйте")

    async with httpx.AsyncClient(timeout=30) as http:
        print(f"\n→ POST {url}")
        print(f"  payload: {json.dumps(payload, ensure_ascii=False)}")
        r = await http.post(url, headers=headers, json=payload)
        print(f"\nstatus={r.status_code}  bytes={len(r.content)}")
        if r.status_code < 400:
            try:
                d = r.json()
                print(f"  ok body: {json.dumps(d, ensure_ascii=False, indent=2)[:1000]}")
            except Exception:
                print(f"  raw body: {r.text[:500]!r}")
        else:
            print(f"  err body: {r.text[:500]!r}")

        # Show response auth headers — Devise rotates tokens on each call
        for h in ("access-token", "client", "uid", "expiry"):
            v = r.headers.get(h)
            if v:
                print(f"  resp header {h}: {v}")


if __name__ == "__main__":
    if len(sys.argv) < 4:
        sys.exit(
            "Usage: python scripts/test_potok_communicate_frontend.py "
            "<job_id> <applicant_id> <channel_id>"
        )
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]))
