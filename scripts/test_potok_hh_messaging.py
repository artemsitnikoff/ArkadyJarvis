#!/usr/bin/env python3
"""Discovery: find the Potok endpoint for sending messages to HH candidates.

Tries common message-related GET endpoints to see which exist.
Pass an applicant_id who has HH source — we'll use it as the test target.

    docker compose exec bot python scripts/test_potok_hh_messaging.py 57921516
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from app.services.potok_client import PotokClient  # noqa: E402


async def try_get(potok, url):
    print(f"\n→ GET {url}")
    try:
        r = await potok._client.get(url)
        print(f"  status={r.status_code}  bytes={len(r.content)}")
        if r.status_code < 400:
            try:
                data = r.json()
                if isinstance(data, dict):
                    keys = list(data.keys())
                    print(f"  keys: {keys[:15]}")
                    if "objects" in data:
                        objs = data["objects"]
                        if isinstance(objs, list):
                            print(f"  objects: list of {len(objs)}")
                            if objs:
                                print(f"  first: {json.dumps(objs[0], ensure_ascii=False)[:500]}")
                    else:
                        print(f"  body[:500]: {json.dumps(data, ensure_ascii=False)[:500]}")
                elif isinstance(data, list):
                    print(f"  list of {len(data)}")
                    if data:
                        print(f"  first: {json.dumps(data[0], ensure_ascii=False)[:500]}")
            except Exception as e:
                print(f"  json parse: {e} — body[:200]={r.text[:200]!r}")
        elif r.status_code == 404:
            pass  # silent — common for non-existent endpoints
        else:
            print(f"  body[:200]: {r.text[:200]!r}")
    except Exception as e:
        print(f"  exception: {e}")


async def main(applicant_id: int) -> None:
    potok = PotokClient()
    try:
        # Show applicant's HH info first
        r = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
        if r.status_code == 200:
            d = r.json()
            print(f"\nApplicant {applicant_id}: {d.get('name')}")
            print(f"  source_type: {d.get('source_type')!r}")
            print(f"  source_url:  {d.get('source_url')!r}")
            accs = d.get("accounts") or []
            print(f"  accounts: {accs}")
            print(f"  HH-related top-level keys: {[k for k in d.keys() if 'hh' in k.lower() or 'head' in k.lower() or 'message' in k.lower() or 'negotiation' in k.lower() or 'vacancy_response' in k.lower()]}")

        candidates_to_try = [
            f"/api/v3/applicants/{applicant_id}/messages.json",
            f"/api/v3/applicants/{applicant_id}/hh_messages.json",
            f"/api/v3/applicants/{applicant_id}/negotiations.json",
            f"/api/v3/applicants/{applicant_id}/vacancy_responses.json",
            f"/api/v3/applicants/{applicant_id}/communications.json",
            f"/api/v3/applicants/{applicant_id}/replies.json",
            f"/api/v3/messages.json?applicant_id={applicant_id}",
            f"/api/v3/messages.json",
            f"/api/v3/hh/messages.json",
            f"/api/v3/hh/applicants/{applicant_id}/messages.json",
            f"/api/v3/integrations/hh/messages.json",
            f"/api/v2/applicants/{applicant_id}/messages.json",
            # Sourcing specific
            f"/api/v3/applicants/{applicant_id}/sources.json",
        ]
        for url in candidates_to_try:
            await try_get(potok, url)

        # Also dump full applicant detail truncated — search for hidden message-related fields
        print("\n\n=== Full applicant detail (search for messaging-related fields) ===")
        if r.status_code == 200:
            d = r.json()
            full_json = json.dumps(d, ensure_ascii=False, indent=2)
            for needle in ("message", "negotiation", "vacancy_response", "hh_", "communicat"):
                idx = full_json.lower().find(needle)
                if idx >= 0:
                    print(f"\nFound {needle!r} at offset {idx} — context:")
                    print(full_json[max(0, idx - 60):idx + 400])
    finally:
        await potok.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/test_potok_hh_messaging.py <applicant_id>")
    asyncio.run(main(int(sys.argv[1])))
