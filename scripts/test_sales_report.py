#!/usr/bin/env python3
"""Прогнать сбор + AI-резюме отдела продаж прямо сейчас (без ожидания 19:00)
и **отправить отчёт всем получателям из SALES_REPORT_RECIPIENTS** (DM).

    docker compose exec bot python scripts/test_sales_report.py [bitrix_user_id] [days] [--no-send]

`days` — 1 (по-умолчанию, сегодня), 7 — за неделю, 30 — за месяц.
`--no-send` — только напечатать в консоль, никому не отправлять.
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

from app.bot.create import create_bot  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.bitrix_client import BitrixClient  # noqa: E402
from app.services.openrouter_client import OpenRouterClient  # noqa: E402
from app.services.prompts import load_prompt  # noqa: E402
from app.services.sales_analytics import (  # noqa: E402
    build_transcripts_bundle,
    collect_for_user_ids,
)


async def main() -> None:
    args = [a for a in sys.argv[1:]]
    no_send = "--no-send" in args
    args = [a for a in args if a != "--no-send"]

    if args:
        bitrix_ids = [int(args[0])]
    else:
        raw = settings.sales_report_bitrix_user_ids
        if not raw:
            sys.exit("Pass a Bitrix user_id arg, or set SALES_REPORT_BITRIX_USER_IDS in .env")
        bitrix_ids = [int(x) for x in raw.split(",") if x.strip()]
    days = int(args[1]) if len(args) > 1 else 1

    recipients_raw = settings.sales_report_recipients.strip()
    recipients = [int(x.strip()) for x in recipients_raw.split(",") if x.strip()]

    print(f"\nCollecting activity for Bitrix user IDs: {bitrix_ids}")
    print(f"Period: last {days} day(s)")
    print(f"Timezone: {settings.timezone}")
    print(f"Recipients ({len(recipients)}): {recipients}")
    print(f"Send mode: {'DRY-RUN (no send)' if no_send else 'LIVE — отправляю в Telegram'}\n")

    bitrix = BitrixClient()
    ai = AIClient()
    openrouter = OpenRouterClient()
    bot = create_bot() if not no_send and recipients else None
    try:
        # Транскрипция всегда включена — есть лимит max_transcripts=25 в самом сервисе
        activities = await collect_for_user_ids(
            bitrix, bitrix_ids, settings.timezone, period_days=days,
            openrouter=openrouter, ai_client=ai, with_transcripts=True,
        )
        print(f"=== RAW ACTIVITY JSON ===")
        def _to_dict(a):
            d = a.__dict__.copy()
            d["calls"] = [c.__dict__ for c in a.calls]
            return d
        dumped = [_to_dict(a) for a in activities]
        print(json.dumps(dumped, ensure_ascii=False, indent=2, default=str)[:5000])

        print("\n=== AI SUMMARY ===")
        prompt = (
            load_prompt("sales_summary")
            .replace("{dc_context}", load_prompt("digital_clouds_context"))
            .replace("{data_json}", json.dumps(dumped, ensure_ascii=False, indent=2, default=str))
        )
        summary = await ai.complete(prompt, timeout=180)
        print(summary)

        if bot and recipients:
            tag = "#анализ_отдела_продаж" if days == 1 else (
                "#анализ_отдела_продаж_неделя" if days == 7 else f"#анализ_отдела_продаж_{days}д"
            )
            text = f"{tag} (test)\n\n{summary}"
            bundle = build_transcripts_bundle(activities)
            bundle_bytes = bundle.encode("utf-8") if bundle.strip() else None
            print(f"\n=== SENDING to {len(recipients)} recipients ===")
            from aiogram.types import BufferedInputFile
            for tg_id in recipients:
                try:
                    await bot.send_message(tg_id, text)
                    if bundle_bytes:
                        file = BufferedInputFile(
                            bundle_bytes,
                            filename=f"calls_transcripts_{days}d.md",
                        )
                        await bot.send_document(tg_id, file)
                    print(f"  ✅ sent to {tg_id}")
                except Exception as e:
                    print(f"  ❌ {tg_id}: {e}")
    finally:
        await bitrix.close()
        await ai.close()
        await openrouter.close()
        if bot:
            await bot.session.close()


asyncio.run(main())
