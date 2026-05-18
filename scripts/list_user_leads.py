#!/usr/bin/env python3
"""List all active leads of a Bitrix user grouped by status.

    docker compose exec bot python scripts/list_user_leads.py 697
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.WARNING, format="%(levelname)s: %(message)s")

from app.services.bitrix_client import BitrixClient  # noqa: E402


async def main(user_id: int) -> None:
    bitrix = BitrixClient()
    try:
        all_leads = []
        start = 0
        while True:
            r = await bitrix._request("crm.lead.list", {
                "filter": {"ASSIGNED_BY_ID": user_id, "!STATUS_ID": ["JUNK", "CONVERTED"]},
                "select": ["ID", "TITLE", "STATUS_ID", "DATE_CREATE", "SOURCE_ID", "DATE_MODIFY"],
                "start": start,
            })
            leads = r.get("result") or []
            all_leads.extend(leads)
            next_start = r.get("next")
            if next_start is None:
                break
            start = next_start

        print(f"\nВсего активных лидов (исключены JUNK и CONVERTED): {len(all_leads)}\n")

        # Группируем по статусам
        by_status: dict[str, list[dict]] = {}
        for l in all_leads:
            by_status.setdefault(l.get("STATUS_ID") or "—", []).append(l)

        for status in sorted(by_status.keys()):
            items = by_status[status]
            print(f"━━━ Статус: {status} ({len(items)}) ━━━")
            for l in items:
                title = (l.get("TITLE") or "").strip()
                src = l.get("SOURCE_ID") or ""
                dc = (l.get("DATE_CREATE") or "")[:10]
                print(f"  {l.get('ID'):>6}  {dc}  src={src:<15} {title[:80]}")
            print()
    finally:
        await bitrix.close()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit("Usage: python scripts/list_user_leads.py <bitrix_user_id>")
    asyncio.run(main(int(sys.argv[1])))
