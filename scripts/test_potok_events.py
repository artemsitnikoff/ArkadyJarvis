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

            if etype != "Event::Comment" or not body:
                continue

            # Try JARVIS marker
            match = re.search(r"<!-- JARVIS:QUESTIONS:(.*?) -->", body, re.DOTALL)
            if match:
                questions = json.loads(match.group(1))
                print(f"  ✅ JARVIS marker found! Questions ({len(questions)}):")
                for q in questions:
                    print(f"    - {q}")
                continue

            # Try legacy HTML parse
            if "Вопросы для первого контакта" in body:
                section = re.search(
                    r"Вопросы для первого контакта.*?<ul>(.*?)</ul>",
                    body, re.DOTALL | re.IGNORECASE,
                )
                if section:
                    items = re.findall(r"<li>(.*?)</li>", section.group(1), re.DOTALL)
                    questions = [re.sub(r"<[^>]+>", "", q).strip() for q in items if q.strip()]
                    print(f"  ✅ Legacy HTML parse found! Questions ({len(questions)}):")
                    for q in questions:
                        print(f"    - {q}")
                else:
                    print(f"  ⚠️  'Вопросы' heading found but <ul> not matched")
                    idx = body.find("Вопросы")
                    print(f"  Snippet: ...{body[max(0,idx-10):idx+300]}...")
            elif "🤖 Оценка AI" in body:
                print(f"  ℹ️  AI comment (no questions section)")
                print(f"  Body snippet: {body[:200]!r}")

asyncio.run(main())
