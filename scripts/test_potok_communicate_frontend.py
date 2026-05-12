#!/usr/bin/env python3
"""Test the Potok frontend communicate endpoint using DeviseTokenAuth tokens
captured from the browser DevTools, with auto-extraction of HH channel_id
from the applicant's accounts[].url ?t=... query parameter.

Set env vars (from browser DevTools, Network → request headers):
    POTOK_FRONTEND_ACCESS_TOKEN=...
    POTOK_FRONTEND_CLIENT=...
    POTOK_FRONTEND_UID=...

Run:
    docker compose exec \
      -e POTOK_FRONTEND_ACCESS_TOKEN=... \
      -e POTOK_FRONTEND_CLIENT=... \
      -e POTOK_FRONTEND_UID=... \
      bot python scripts/test_potok_communicate_frontend.py <job_id> <applicant_id> [message]
"""
import asyncio
import json
import os
import sys
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx  # noqa: E402

from app.services.potok_client import PotokClient  # noqa: E402


def extract_hh_channel_ids(accounts: list) -> list[str]:
    """Pick `t=<channel_id>` from every headhunter account URL."""
    channels: list[str] = []
    for acc in accounts or []:
        if acc.get("icon_id") != "headhunter":
            continue
        url = acc.get("url") or ""
        q = parse_qs(urlparse(url).query)
        t = (q.get("t") or [None])[0]
        if t:
            channels.append(t)
    return channels


async def fetch_applicant_accounts(applicant_id: int) -> tuple[list[str], str]:
    """Fetch applicant via public API, return (channel_ids, name)."""
    potok = PotokClient()
    try:
        r = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
        r.raise_for_status()
        d = r.json()
        return extract_hh_channel_ids(d.get("accounts") or []), d.get("name") or ""
    finally:
        await potok.close()


async def main(job_id: int, applicant_id: int, message: str) -> None:
    access_token = os.environ.get("POTOK_FRONTEND_ACCESS_TOKEN", "").strip()
    client = os.environ.get("POTOK_FRONTEND_CLIENT", "").strip()
    uid = os.environ.get("POTOK_FRONTEND_UID", "").strip()
    if not (access_token and client and uid):
        sys.exit("Set POTOK_FRONTEND_ACCESS_TOKEN / POTOK_FRONTEND_CLIENT / POTOK_FRONTEND_UID")

    channels, name = await fetch_applicant_accounts(applicant_id)
    print(f"\nApplicant: {name} (id={applicant_id})")
    if not channels:
        print("  ❌ No HH channels found in accounts[].url — candidate has no active HH negotiation")
        return
    print(f"  HH channels: {channels}")

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
    payload = {
        "communication_envelopes": [
            {
                "provider": "headhunter",
                "channels": channels,
                "message": {"body": message},
            }
        ]
    }
    url = f"https://app.potok.io/client_api/jobs/{job_id}/{applicant_id}/communication/communicate.json"

    async with httpx.AsyncClient(timeout=30) as http:
        print(f"\n→ POST {url}")
        print(f"  payload: {json.dumps(payload, ensure_ascii=False)}")
        r = await http.post(url, headers=headers, json=payload)
        print(f"\nstatus={r.status_code}  bytes={len(r.content)}")
        body = r.text[:500]
        if r.status_code < 400:
            print(f"  ok body: {body!r}")
        else:
            print(f"  err body: {body!r}")

        # Show rotated auth headers
        for h in ("access-token", "client", "uid", "expiry"):
            v = r.headers.get(h)
            if v:
                print(f"  resp header {h}: {v}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(
            "Usage: python scripts/test_potok_communicate_frontend.py "
            "<job_id> <applicant_id> [message]"
        )
    msg = sys.argv[3] if len(sys.argv) > 3 else "Тест отправки через API — пожалуйста, проигнорируйте"
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2]), msg))
