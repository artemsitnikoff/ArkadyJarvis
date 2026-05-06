#!/usr/bin/env python3
"""Brute-force search for the correct Potok stage-change endpoint.

Tries a long list of plausible (method, url, payload) tuples and after
each one re-fetches the applicant to see if the stage actually moved.
Stops at the first one that actually works.
"""
import asyncio
import json as jsonmod
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")
logging.getLogger("arkadyjarvis").setLevel(logging.WARNING)

from app.services.potok_client import PotokClient  # noqa: E402

# ── EDIT THESE ──
APPLICANT_ID = 57921516
JOB_ID = 1589325
TARGET_STAGE_NAME = "Скриннинг резюме"


def safe_json(resp):
    try:
        return resp.json()
    except Exception:
        return None


async def get_current_stage(potok, applicant_id, job_id):
    r = await potok._client.get(f"/api/v3/applicants/{applicant_id}.json")
    data = safe_json(r) or {}
    for j in data.get("ajs_joins") or []:
        if (j.get("job") or {}).get("id") == job_id:
            return j.get("id"), (j.get("stage") or {}).get("name"), (j.get("stage") or {}).get("id")
    return None, None, None


async def get_target_stage_id(potok, job_id, stage_name):
    r = await potok._client.get(f"/api/v3/jobs/{job_id}.json")
    data = safe_json(r) or {}
    for s in data.get("stages") or []:
        if (s.get("name") or "").strip().lower() == stage_name.strip().lower():
            return s.get("id")
    return None


async def main() -> None:
    potok = PotokClient()
    try:
        ajs_join_id, current_name, current_id = await get_current_stage(potok, APPLICANT_ID, JOB_ID)
        target_id = await get_target_stage_id(potok, JOB_ID, TARGET_STAGE_NAME)

        if not ajs_join_id or not target_id:
            print(f"❌ ajs_join_id={ajs_join_id}, target_id={target_id} — bail")
            return

        if current_id == target_id:
            print(f"Already in target stage {TARGET_STAGE_NAME!r} — pick a different applicant or different stage to test")
            return

        print(f"applicant={APPLICANT_ID}  job={JOB_ID}  ajs_join={ajs_join_id}")
        print(f"current stage:  {current_name!r} (id={current_id})")
        print(f"target  stage:  {TARGET_STAGE_NAME!r} (id={target_id})\n")

        attempts = [
            # PATCH variants
            ("PATCH", f"/api/v3/ajs_joins/{ajs_join_id}.json", {"ajs_join": {"stage_id": target_id}}),
            ("PATCH", f"/api/v3/ajs_joins/{ajs_join_id}.json", {"stage_id": target_id}),
            ("PATCH", f"/api/v3/ajs_joins/{ajs_join_id}.json", {"ajs_join": {"stage": {"id": target_id}}}),
            ("PATCH", f"/api/v3/ajs_joins/{ajs_join_id}", {"ajs_join": {"stage_id": target_id}}),
            ("PATCH", f"/api/v3/applicants/{APPLICANT_ID}/ajs_joins/{ajs_join_id}.json", {"ajs_join": {"stage_id": target_id}}),
            ("PATCH", f"/api/v3/applicants/{APPLICANT_ID}.json", {"applicant": {"stage_id": target_id, "job_id": JOB_ID}}),
            ("PUT", f"/api/v3/ajs_joins/{ajs_join_id}.json", {"ajs_join": {"stage_id": target_id}}),
            # Event::Stage variants
            ("POST", "/api/v3/events.json", {"event": {"type": "Event::Stage", "applicant_id": APPLICANT_ID, "job_id": JOB_ID, "stage_id": target_id}}),
            ("POST", "/api/v3/events.json", {"event": {"type": "Event::Stage", "applicant_id": APPLICANT_ID, "job_id": JOB_ID, "to_stage_id": target_id}}),
            ("POST", "/api/v3/events.json", {"event": {"type": "Event::Stage", "applicant_id": APPLICANT_ID, "job_id": JOB_ID, "ajs_join_id": ajs_join_id, "stage_id": target_id}}),
            # Action endpoints
            ("POST", f"/api/v3/ajs_joins/{ajs_join_id}/move.json", {"stage_id": target_id}),
            ("POST", f"/api/v3/ajs_joins/{ajs_join_id}/change_stage.json", {"stage_id": target_id}),
            ("POST", f"/api/v3/applicants/{APPLICANT_ID}/move_to_stage.json", {"job_id": JOB_ID, "stage_id": target_id}),
            ("POST", f"/api/v3/applicants/{APPLICANT_ID}/change_stage.json", {"job_id": JOB_ID, "stage_id": target_id}),
            # V2 fallbacks
            ("PATCH", f"/api/v2/ajs_joins/{ajs_join_id}.json", {"ajs_join": {"stage_id": target_id}}),
            ("POST", f"/api/v2/ajs_joins/{ajs_join_id}/change_stage.json", {"stage_id": target_id}),
        ]

        for i, (method, url, body) in enumerate(attempts, 1):
            print(f"[{i:2d}/{len(attempts)}] {method} {url}")
            print(f"        payload: {jsonmod.dumps(body, ensure_ascii=False)[:150]}")
            try:
                resp = await potok._client.request(method, url, json=body)
                print(f"        → {resp.status_code}  body[:200]={resp.text[:200]!r}")
            except Exception as e:
                print(f"        → exception: {e}")
                continue

            # Verify
            _, new_name, new_id = await get_current_stage(potok, APPLICANT_ID, JOB_ID)
            if new_id == target_id:
                print(f"\n🎉 WINNER: {method} {url}")
                print(f"   payload: {jsonmod.dumps(body, ensure_ascii=False)}")
                print(f"   stage moved: {current_name!r} → {new_name!r}")
                return
            else:
                print(f"        verify: stage is still {new_name!r} (id={new_id}) — not moved\n")

        print("\n❌ All attempts exhausted — none of the endpoints actually moved the stage.")
    finally:
        await potok.close()


asyncio.run(main())
