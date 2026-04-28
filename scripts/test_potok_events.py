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

        # We now know events live in applicant detail under "events" key
        url = f"/api/v3/applicants/{APPLICANT_ID}.json"
        resp = await client.get(url)
        print(f"\nGET {url}  →  {resp.status_code}")
        data = resp.json()
        events = data.get("events", [])
        print(f"Total events: {len(events)}")

        for event in events:
            eid = event.get("id")
            etype = event.get("type")
            body = event.get("body") or ""
            print(f"\n  Event id={eid} type={etype} body_len={len(body)}")
            if "JARVIS" in body:
                print(f"  *** FOUND JARVIS MARKER ***")
                match = re.search(r"<!-- JARVIS:QUESTIONS:(.*?) -->", body, re.DOTALL)
                if match:
                    questions = json.loads(match.group(1))
                    print(f"  Questions ({len(questions)}):")
                    for q in questions:
                        print(f"    - {q}")
                else:
                    print(f"  Marker found but regex didn't match. Body snippet:")
                    idx = body.find("JARVIS")
                    print(f"  ...{body[max(0,idx-20):idx+200]}...")
            elif etype == "Event::Comment":
                print(f"  Body snippet: {body[:150]!r}")

asyncio.run(main())
