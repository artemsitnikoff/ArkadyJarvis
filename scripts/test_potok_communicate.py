#!/usr/bin/env python3
"""Probe whether Potok exposes the /communication/communicate.json endpoint
on the public Bearer-auth API (we only have that — not the cookie-based
client_api the frontend uses).

Run:
    docker compose exec bot python scripts/test_potok_communicate.py 1596058 58261774
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from app.services.potok_client import PotokClient  # noqa: E402

# Probe payload — keep it minimal but recognizable. We'll send to a test
# applicant; if endpoint is real and accepts our auth, expect 200 / 422 / 401.
TEST_PAYLOAD = {
    "subject": "Тест",
    "text": "Тестовое сообщение от API (не отправляйте кандидату)",
    "channel": "hh",
}


async def probe(potok, method, url, body=None):
    print(f"\n→ {method} {url}")
    if body is not None:
        print(f"  payload: {json.dumps(body, ensure_ascii=False)[:200]}")
    try:
        r = await potok._client.request(method, url, json=body)
        print(f"  status={r.status_code}  bytes={len(r.content)}")
        if r.status_code < 400:
            try:
                d = r.json()
                print(f"  ok body: {json.dumps(d, ensure_ascii=False)[:500]}")
            except Exception:
                print(f"  ok body (raw): {r.text[:300]!r}")
        else:
            print(f"  err body: {r.text[:300]!r}")
    except Exception as e:
        print(f"  exception: {e}")


async def main(job_id: int, applicant_id: int) -> None:
    potok = PotokClient()
    try:
        # First: GET (probably 404/405 — but tells us if URL exists at all)
        await probe(potok, "GET", f"/api/v3/jobs/{job_id}/{applicant_id}/communication/communicate.json")
        # Then: real POST variants
        urls = [
            f"/api/v3/jobs/{job_id}/{applicant_id}/communication/communicate.json",
            f"/api/v3/jobs/{job_id}/applicants/{applicant_id}/communication/communicate.json",
            f"/api/v3/jobs/{job_id}/applicants/{applicant_id}/communicate.json",
            f"/api/v3/applicants/{applicant_id}/communicate.json",
            f"/api/v3/applicants/{applicant_id}/communication.json",
            f"/api/v3/applicants/{applicant_id}/send_message.json",
            f"/api/v2/jobs/{job_id}/{applicant_id}/communication/communicate.json",
            # Also try with the client_api prefix (in case it accepts Bearer too)
            f"/client_api/jobs/{job_id}/{applicant_id}/communication/communicate.json",
        ]
        for url in urls:
            await probe(potok, "POST", url, body=TEST_PAYLOAD)
    finally:
        await potok.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("Usage: python scripts/test_potok_communicate.py <job_id> <applicant_id>")
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
