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

async def _list_all_jobs(potok: PotokClient) -> list[dict]:
    jobs = []
    for scope in ("active", "archived"):
        try:
            r = await potok._client.get(
                "/api/v3/cursor_paginated/jobs.json",
                params={"by_scope": scope, "per_page": 100},
            )
            r.raise_for_status()
            d = r.json()
            jobs.extend(d.get("objects", {}).get("jobs", []))
        except Exception as e:
            print(f"  warning: failed to list {scope} jobs: {e}")
    return jobs


async def _search_job(potok: PotokClient, job: dict, needle: str) -> list[tuple]:
    job_id = job["id"]
    matches: list[tuple] = []
    cursor = None
    while True:
        params: dict = {"per_page": 100}
        if cursor:
            params["page_cursor"] = cursor
        try:
            r = await potok._client.get(f"/api/v3/jobs/{job_id}/ajs_joins.json", params=params)
            if r.status_code != 200:
                break
            d = r.json()
        except Exception:
            break
        for obj in d.get("objects", []):
            applicant_id = obj.get("applicant_id")
            try:
                ar = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
                if ar.status_code != 200 or not ar.content:
                    continue
                ad = ar.json()
            except Exception:
                continue
            full = " ".join(filter(None, [
                ad.get("last_name"), ad.get("first_name"), ad.get("middle_name"), ad.get("name"),
            ])).lower()
            if needle in full:
                matches.append((applicant_id, ad, obj, job))
        if not d.get("has_next_page"):
            break
        cursor = d.get("page_next_cursor")
    return matches


async def main(name_fragment: str) -> None:
    potok = PotokClient()
    try:
        jobs = await _list_all_jobs(potok)
        print(f"Scanning {len(jobs)} jobs for name fragment {name_fragment!r}…\n")

        needle = name_fragment.strip().lower()
        all_matches: list[tuple] = []
        for i, job in enumerate(jobs, 1):
            print(f"  [{i:3d}/{len(jobs)}] job {job['id']}: {job.get('name', '')!r}", end="", flush=True)
            ms = await _search_job(potok, job, needle)
            if ms:
                print(f"  ← {len(ms)} match(es)")
                all_matches.extend(ms)
            else:
                print()

        print(f"\n{'='*70}\nTotal matches: {len(all_matches)}\n{'='*70}")
        for applicant_id, ad, ajs_join_summary, job in all_matches:
            print(f"\n[ Job: {job.get('name')!r}  (id={job['id']}) ]")
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
