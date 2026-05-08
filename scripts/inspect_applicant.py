#!/usr/bin/env python3
"""Find applicants in a job by name fragment and dump their raw Potok data.

Use to debug "ghost" candidates: people the bot shows but Potok UI hides.

Run:
    docker compose exec bot python scripts/inspect_applicant.py "Падалка"
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from app.services.potok_client import PotokClient  # noqa: E402

# ── EDIT THIS ──
JOB_ID = 1589325  # the job you're scanning


async def main(name_fragment: str) -> None:
    potok = PotokClient()
    try:
        # Get all applicant IDs in this job (paginated ajs_joins)
        ajs_join_objs = []
        cursor = None
        while True:
            params: dict = {"per_page": 100}
            if cursor:
                params["page_cursor"] = cursor
            r = await potok._client.get(f"/api/v3/jobs/{JOB_ID}/ajs_joins.json", params=params)
            r.raise_for_status()
            d = r.json()
            ajs_join_objs.extend(d.get("objects", []))
            if not d.get("has_next_page"):
                break
            cursor = d.get("page_next_cursor")

        print(f"Job {JOB_ID}: {len(ajs_join_objs)} ajs_joins total")
        print(f"Searching for name fragment {name_fragment!r}…\n")

        needle = name_fragment.strip().lower()
        matches = []
        for i, obj in enumerate(ajs_join_objs, 1):
            applicant_id = obj.get("applicant_id")
            try:
                ar = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
                if ar.status_code != 200 or not ar.content:
                    continue
                ad = ar.json()
            except Exception as e:
                print(f"  [{i}] applicant {applicant_id} fetch error: {e}")
                continue

            full = " ".join(filter(None, [
                ad.get("last_name"), ad.get("first_name"), ad.get("middle_name"), ad.get("name"),
            ])).lower()
            if needle in full:
                matches.append((applicant_id, ad, obj))
                print(f"  [{i}] MATCH: applicant_id={applicant_id}  full_name={full!r}")

            if i % 20 == 0:
                print(f"  scanned {i}/{len(ajs_join_objs)}…")

        print(f"\nMatches: {len(matches)}")
        for applicant_id, ad, ajs_join_summary in matches:
            print("\n" + "=" * 70)
            print(f"applicant_id = {applicant_id}")
            print(f"name         = {ad.get('name')}")
            print(f"last_name    = {ad.get('last_name')}")
            print(f"first_name   = {ad.get('first_name')}")
            print(f"middle_name  = {ad.get('middle_name')}")
            print(f"phones       = {ad.get('phones')}")
            print(f"created_at   = {ad.get('created_at')}")
            print(f"updated_at   = {ad.get('updated_at')}")
            print(f"source_type  = {ad.get('source_type')}")
            print(f"source_url   = {ad.get('source_url')}")

            print("\najs_join (summary from /jobs/{job}/ajs_joins.json):")
            print(f"  {json.dumps(ajs_join_summary, ensure_ascii=False, indent=2)[:600]}")

            print("\najs_joins on applicant detail:")
            for j in ad.get("ajs_joins") or []:
                if (j.get("job") or {}).get("id") == JOB_ID:
                    print(f"  {json.dumps(j, ensure_ascii=False, indent=2)[:600]}")

            # Show top-level keys that might indicate archived/merged status
            extras = {
                k: ad.get(k) for k in (
                    "archived", "deleted", "deleted_at", "merged", "merged_into_id",
                    "is_archived", "is_deleted", "state_id", "state",
                )
                if k in ad
            }
            if extras:
                print(f"\nflag-like fields: {extras}")
    finally:
        await potok.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/inspect_applicant.py <name_fragment>")
    asyncio.run(main(sys.argv[1]))
