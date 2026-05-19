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


async def main() -> None:
    await init_db()
    bitrix = BitrixClient()
    try:
        print("\n=== 1. Парсю DCJ.xlsx ===")
        ins, upd = await import_dcj_xlsx("DCJ.xlsx")
        print(f"  inserted={ins}  updated={upd}")

        print("\n=== 2. Сидаю менеджеров (резолв через Bitrix) ===")
        rows, warnings = await seed_default_managers(bitrix)
        print(f"  rows={rows}")
        if warnings:
            print(f"  warnings ({len(warnings)}):")
            for w in warnings:
                print(f"    ⚠️  {w}")

        print("\n=== 3. Менеджеры ===")
        for m in await list_managers():
            print(f"  {m['manager_name']:<20} bitrix_id={m['manager_bitrix_id']!s:<5} devs={m['dev_count']}")

        print("\n=== 4. Разработчики ===")
        for d in await list_developers():
            email = d["developer_email"] or "—"
            print(
                f"  {d['developer_pattern']:<30} мгр={d['manager_name']:<15} "
                f"bitrix_id={d['developer_bitrix_id']!s:<5} email={email}"
            )
    finally:
        await bitrix.close()
        await close_db()


asyncio.run(main())
