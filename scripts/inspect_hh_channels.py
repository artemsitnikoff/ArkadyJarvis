#!/usr/bin/env python3
"""Show why a given candidate landed in `no_channel` — dump phones, accounts,
and HH channel IDs (?t=...) for one or more candidates by name fragment.

Pass job_id then one or more name fragments:
    docker compose exec bot python scripts/inspect_hh_channels.py 1596058 "Новицкий" "Крылов" "Талалаев" "Белеванцев"
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from app.services.potok_client import PotokClient  # noqa: E402
from app.services.potok_frontend import extract_hh_channels  # noqa: E402


async def main(job_id: int, fragments: list[str]) -> None:
    potok = PotokClient()
    try:
        # Get all ajs_joins for this job (with active=true filter)
        ajs_joins_objs = []
        cursor = None
        while True:
            params: dict = {"per_page": 100}
            if cursor:
                params["page_cursor"] = cursor
            r = await potok._client.get(f"/api/v3/jobs/{job_id}/ajs_joins.json", params=params)
            r.raise_for_status()
            d = r.json()
            ajs_joins_objs.extend(d.get("objects", []))
            if not d.get("has_next_page"):
                break
            cursor = d.get("page_next_cursor")

        print(f"Job {job_id}: {len(ajs_joins_objs)} ajs_joins")

        for i, obj in enumerate(ajs_joins_objs, 1):
            applicant_id = obj.get("applicant_id")
            try:
                r = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
                if r.status_code != 200 or not r.content:
                    continue
                ad = r.json()
            except Exception:
                continue
            full = " ".join(filter(None, [
                ad.get("last_name"), ad.get("first_name"), ad.get("middle_name"), ad.get("name"),
            ])).lower()
            matched = next((f for f in fragments if f.lower() in full), None)
            if not matched:
                continue

            print(f"\n{'='*70}")
            print(f"📋 {ad.get('name')} (id={applicant_id})")
            print(f"   matched fragment: {matched!r}")
            print(f"   source_type:  {ad.get('source_type')!r}")
            print(f"   phones:       {ad.get('phones')!r}")
            print(f"   active(ajs):  {obj.get('active')!r}")

            accounts = ad.get("accounts") or []
            print(f"\n   accounts ({len(accounts)}):")
            for acc in accounts:
                icon = acc.get("icon_id")
                url = acc.get("url")
                print(f"     • icon_id={icon!r}  url={url!r}")

            channels = extract_hh_channels(accounts)
            print(f"\n   → extracted HH channels (?t=): {channels}")
            if not channels:
                hh_urls = [a.get("url") for a in accounts if (a or {}).get("icon_id") == "headhunter"]
                if hh_urls:
                    print(f"   ↳ has HH urls but no ?t= → no active HH negotiation")
                else:
                    print(f"   ↳ no HH urls at all")
    finally:
        await potok.close()


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit("Usage: python scripts/inspect_hh_channels.py <job_id> <name1> [<name2> ...]")
    asyncio.run(main(int(sys.argv[1]), sys.argv[2:]))
