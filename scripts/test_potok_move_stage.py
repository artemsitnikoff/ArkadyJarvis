#!/usr/bin/env python3
"""Debug script: verify Potok stage-move API endpoints.

Edit APPLICANT_ID and JOB_ID below, then run:
    docker compose exec bot python scripts/test_potok_move_stage.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

from app.config import settings  # noqa: E402
from app.services.potok_client import PotokClient  # noqa: E402

# ── EDIT THESE ──
APPLICANT_ID = 57921516
JOB_ID = 19382504
TARGET_STAGE_NAME = settings.potok_after_contact_stage  # "Скрининг резюме" by default


async def main() -> None:
    potok = PotokClient()
    try:
        # Show current stage and available stages
        resp = await potok._client.get(f"/api/v3/applicants/{APPLICANT_ID}.json")
        resp.raise_for_status()
        data = resp.json()
        for j in data.get("ajs_joins") or []:
            if (j.get("job") or {}).get("id") == JOB_ID:
                stage = (j.get("stage") or {}).get("name")
                print(f"Current stage of applicant {APPLICANT_ID} in job {JOB_ID}: {stage!r}")
                print(f"ajs_join_id = {j.get('id')}")
                break

        for endpoint in [f"/api/v3/jobs/{JOB_ID}.json", f"/api/v2/jobs/{JOB_ID}.json"]:
            r = await potok._client.get(endpoint)
            if r.status_code < 400:
                stages = r.json().get("stages") or []
                print(f"\nStages in job {JOB_ID} (via {endpoint}):")
                for s in stages:
                    print(f"  • id={s.get('id')}  name={s.get('name')!r}  type={s.get('stage_type')}")
                break

        print(f"\n→ Trying to move to stage {TARGET_STAGE_NAME!r}…")
        ok = await potok.move_applicant_to_stage(APPLICANT_ID, JOB_ID, TARGET_STAGE_NAME)
        print(f"\nResult: {'SUCCESS' if ok else 'FAILED'} — see logs above")

        # Verify
        resp = await potok._client.get(f"/api/v3/applicants/{APPLICANT_ID}.json")
        data = resp.json()
        for j in data.get("ajs_joins") or []:
            if (j.get("job") or {}).get("id") == JOB_ID:
                stage = (j.get("stage") or {}).get("name")
                print(f"After move — stage is now: {stage!r}")
                break
    finally:
        await potok.close()


asyncio.run(main())
