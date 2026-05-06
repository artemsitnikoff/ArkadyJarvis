#!/usr/bin/env python3
"""Debug script: verify Potok stage-move API endpoints.

Edit APPLICANT_ID and JOB_ID below, then run:
    docker compose exec bot python scripts/test_potok_move_stage.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from app.config import settings  # noqa: E402
from app.services.potok_client import PotokClient  # noqa: E402

# ── EDIT THESE ──
APPLICANT_ID = 57921516
JOB_ID = 1589325
TARGET_STAGE_NAME = settings.potok_after_contact_stage  # "Скрининг резюме" by default


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None


async def main() -> None:
    potok = PotokClient()
    try:
        print(f"\n{'='*60}")
        print(f"Applicant: {APPLICANT_ID}")
        print(f"Job:       {JOB_ID}")
        print(f"Target:    {TARGET_STAGE_NAME!r}")
        print(f"{'='*60}\n")

        # 1) Applicant detail
        resp = await potok._client.get(f"/api/v3/applicants/{APPLICANT_ID}.json")
        print(f"GET /api/v3/applicants/{APPLICANT_ID}.json → {resp.status_code} ({len(resp.content)} bytes)")
        data = safe_json(resp)
        if not data:
            print(f"  body: {resp.text[:300]!r}")
            return

        ajs_joins = data.get("ajs_joins") or []
        print(f"  ajs_joins count: {len(ajs_joins)}")
        for j in ajs_joins:
            jid = j.get("id")
            job_info = j.get("job") or {}
            stage_info = j.get("stage") or {}
            print(f"   • ajs_join_id={jid}  job_id={job_info.get('id')}  job_name={job_info.get('name')!r}  stage={stage_info.get('name')!r}")

        target_join = next(
            (j for j in ajs_joins if (j.get("job") or {}).get("id") == JOB_ID),
            None,
        )
        if not target_join:
            print(f"\n❌ Applicant {APPLICANT_ID} has no ajs_join for job {JOB_ID}")
            print("   Pick a different applicant or different job_id and re-run.")
            return

        print(f"\n✅ Found ajs_join {target_join.get('id')} in job {JOB_ID}")
        current_stage = (target_join.get('stage') or {}).get('name')
        print(f"   Current stage: {current_stage!r}\n")

        # 2) Job detail to see stages
        for endpoint in [f"/api/v3/jobs/{JOB_ID}.json", f"/api/v2/jobs/{JOB_ID}.json"]:
            r = await potok._client.get(endpoint)
            print(f"GET {endpoint} → {r.status_code} ({len(r.content)} bytes)")
            jd = safe_json(r)
            if not jd:
                print(f"  body: {r.text[:300]!r}")
                continue
            print(f"  top-level keys: {list(jd.keys())}")
            stages = jd.get("stages") or []
            if stages:
                print(f"  stages ({len(stages)}):")
                for s in stages:
                    print(f"    • id={s.get('id')}  name={s.get('name')!r}  type={s.get('stage_type')}  serial={s.get('serial')}")
                break
            else:
                print(f"  no 'stages' field — full response (first 500): {json.dumps(jd, ensure_ascii=False)[:500]}")

        if not stages:
            print("\n❌ Couldn't load stage list for the job — can't continue.")
            return

        # 3) Try the move
        print(f"\n→ Trying move_applicant_to_stage({APPLICANT_ID}, {JOB_ID}, {TARGET_STAGE_NAME!r})...\n")
        ok = await potok.move_applicant_to_stage(APPLICANT_ID, JOB_ID, TARGET_STAGE_NAME)
        print(f"\nResult: {'✅ SUCCESS' if ok else '❌ FAILED'}")

        # 4) Verify by re-fetching applicant
        resp2 = await potok._client.get(f"/api/v3/applicants/{APPLICANT_ID}.json")
        data2 = safe_json(resp2)
        if data2:
            for j in data2.get("ajs_joins") or []:
                if (j.get("job") or {}).get("id") == JOB_ID:
                    new_stage = (j.get('stage') or {}).get('name')
                    print(f"\nAfter move — applicant's stage in job {JOB_ID}: {new_stage!r}")
                    if new_stage != current_stage:
                        print(f"   ↳ stage changed from {current_stage!r} to {new_stage!r}")
                    break

        print(f"\n{'='*60}\nDone.\n")
    finally:
        await potok.close()


asyncio.run(main())
