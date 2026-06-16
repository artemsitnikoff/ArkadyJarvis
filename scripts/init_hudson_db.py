#!/usr/bin/env python3
"""Разово: парсит DCJ.xlsx из корня → таблица dcj_projects.
Сидит маппинг менеджер→разработчики и резолвит Bitrix ID+email по фамилиям.

    docker compose exec bot python scripts/init_hudson_db.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

from app.db import close_db, init_db  # noqa: E402
from app.services.bitrix_client import BitrixClient  # noqa: E402
from app.services.hudson_repo import (  # noqa: E402
    import_dcj_xlsx,
    list_developers,
    list_managers,
    seed_default_managers,
)
from app.services.jira_client import JiraClient  # noqa: E402


async def main() -> None:
    await init_db()
    bitrix = BitrixClient()
    try:
        print("\n=== 1. Парсю DCJ.xlsx ===")
        ins, upd = await import_dcj_xlsx("DCJ.xlsx")
        print(f"  inserted={ins}  updated={upd}")

        print("\n=== 2. Сидаю менеджеров (Bitrix + Jira) ===")
        async with JiraClient() as jira:
            rows, removed, warnings = await seed_default_managers(bitrix, jira=jira)
        print(f"  upserted={rows}  removed={len(removed)}")
        if removed:
            print("  удалены устаревшие привязки:")
            for mgr_name, dev_pat in removed:
                print(f"    🗑️  {mgr_name} / {dev_pat}")
        if warnings:
            print(f"  warnings ({len(warnings)}):")
            for w in warnings:
                print(f"    ⚠️  {w}")

        print("\n=== 3. Менеджеры ===")
        from app.db import get_db
        db = get_db()
        async with db.execute(
            "SELECT manager_name, manager_bitrix_id, manager_jira_username, "
            "COUNT(*) as dev_count FROM hudson_managers GROUP BY manager_name"
        ) as cur:
            for row in await cur.fetchall():
                row = dict(row)
                jira = row.get("manager_jira_username") or "❌ —"
                print(
                    f"  {row['manager_name']:<20} bx={row['manager_bitrix_id']!s:<5} "
                    f"jira={jira:<30} devs={row['dev_count']}"
                )

        print("\n=== 4. Разработчики ===")
        for d in await list_developers():
            email = d["developer_email"] or "—"
            jira_u = d.get("jira_username") or "—"
            print(
                f"  {d['developer_pattern']:<30} мгр={d['manager_name']:<15} "
                f"bx={d['developer_bitrix_id']!s:<5} jira={jira_u:<25} email={email}"
            )
    finally:
        await bitrix.close()
        await close_db()


asyncio.run(main())
