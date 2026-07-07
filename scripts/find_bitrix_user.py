#!/usr/bin/env python3
"""Найти Bitrix user_id по фамилии/имени (partial match, активные юзеры).

    docker compose exec -T bot python scripts/find_bitrix_user.py Добрачков Евенко

Печатает id + ФИО по каждому совпадению. Нужен, чтобы доставать ID для
ID-списков в .env (SALES_REPORT_BITRIX_USER_IDS и пр.) — по имени искать
руками в Bitrix долго. Ищет и по имени, и по фамилии (см. search_users).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.bitrix_client import BitrixClient  # noqa: E402


async def main() -> None:
    queries = sys.argv[1:]
    if not queries:
        sys.exit(
            "Передай одну или несколько фамилий/имён:\n"
            "  find_bitrix_user.py Добрачков Евенко"
        )
    bitrix = BitrixClient()
    try:
        for q in queries:
            users = await bitrix.search_users(q, limit=10)
            if not users:
                print(f"[{q}] — не найдено")
                continue
            for u in users:
                print(f"[{q}] ID={u['id']:>5}  {u['name']}")
    finally:
        await bitrix.close()


asyncio.run(main())
