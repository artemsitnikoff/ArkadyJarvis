#!/usr/bin/env python3
"""Debug script: find the right Potok endpoint for applicant events."""
import asyncio
import json
import re
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from app.config import settings

APPLICANT_ID = 57921516
JOB_ID = 19382504

async def main():
    token = settings.potok_api_token.get_secret_value()
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(base_url=settings.potok_base_url, headers=headers, timeout=30) as client:

        endpoints = [
            f"/api/v3/applicants/{APPLICANT_ID}/events.json",
            f"/api/v2/applicants/{APPLICANT_ID}/events.json",
            f"/api/v3/events.json?applicant_id={APPLICANT_ID}",
            f"/api/v3/applicants/{APPLICANT_ID}.json",
        ]

        for url in endpoints:
            resp = await client.get(url)
            print(f"\n{'='*60}")
            print(f"GET {url}  →  {resp.status_code}")
            if resp.status_code == 200:
                print(f"  Raw body ({len(resp.content)} bytes): {resp.text[:500]!r}")
                if not resp.content:
                    print("  (empty body)")
                    continue
                data = resp.json()
                # Print structure
                if isinstance(data, dict):
                    print(f"Keys: {list(data.keys())}")
                    # Show events/objects if present
                    for key in ("objects", "events", "comments"):
                        if key in data:
                            items = data[key]
                            print(f"  [{key}]: {len(items)} items")
                            for item in items[:3]:
                                print(f"    id={item.get('id')} type={item.get('type')} body_len={len(item.get('body',''))}")
                                body = item.get("body", "")
                                if "JARVIS" in body:
                                    print(f"    *** FOUND JARVIS MARKER ***")
                                    match = re.search(r"<!-- JARVIS:QUESTIONS:(.*?) -->", body, re.DOTALL)
                                    if match:
                                        print(f"    Questions: {json.loads(match.group(1))}")
                                    else:
                                        print(f"    Body snippet: {body[:300]}")
                    # If applicant detail — check events field
                    if "events" not in data and "objects" not in data:
                        print(f"  Full keys: {json.dumps(list(data.keys()), ensure_ascii=False)}")
                elif isinstance(data, list):
                    print(f"  List of {len(data)} items")
            else:
                print(f"  Body: {resp.text[:200]}")

asyncio.run(main())
