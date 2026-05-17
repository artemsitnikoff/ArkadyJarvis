#!/usr/bin/env python3
"""Прогнать сбор + AI-резюме отдела продаж прямо сейчас (без ожидания 19:00).

Использует BitrixClient + AIClient. Печатает JSON активности + готовый отчёт.

    docker compose exec bot python scripts/test_sales_report.py [bitrix_user_id] [days]

`days` — 1 (по-умолчанию, сегодня), 7 — за неделю, 30 — за месяц.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

from app.config import settings  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.bitrix_client import BitrixClient  # noqa: E402
from app.services.prompts import load_prompt  # noqa: E402
from app.services.sales_analytics import collect_for_user_ids  # noqa: E402


async def main() -> None:
    if len(sys.argv) > 1:
        bitrix_ids = [int(sys.argv[1])]
    else:
        raw = settings.sales_report_bitrix_user_ids
        if not raw:
            sys.exit("Pass a Bitrix user_id arg, or set SALES_REPORT_BITRIX_USER_IDS in .env")
        bitrix_ids = [int(x) for x in raw.split(",") if x.strip()]
    days = int(sys.argv[2]) if len(sys.argv) > 2 else 1

    print(f"\nCollecting activity for Bitrix user IDs: {bitrix_ids}")
    print(f"Period: last {days} day(s)")
    print(f"Timezone: {settings.timezone}\n")

    bitrix = BitrixClient()
    ai = AIClient()
    try:
        activities = await collect_for_user_ids(bitrix, bitrix_ids, settings.timezone, period_days=days)
        print("=== RAW ACTIVITY JSON ===")
        dumped = [a.__dict__ for a in activities]
        print(json.dumps(dumped, ensure_ascii=False, indent=2, default=str)[:3000])

        print("\n=== AI SUMMARY ===")
        prompt = load_prompt("sales_summary").replace(
            "{data_json}",
            json.dumps(dumped, ensure_ascii=False, indent=2, default=str),
        )
        summary = await ai.complete(prompt, timeout=120)
        print(summary)
    finally:
        await bitrix.close()
        await ai.close()


asyncio.run(main())
