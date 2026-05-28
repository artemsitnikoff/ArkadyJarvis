#!/usr/bin/env python3
"""Обновляет SOURCE_ID на уже созданных b24-recon лидах — ставит «База Яндекс».

НЕ создаёт новых лидов, не зовёт Claude. Только Bitrix API.

Фильтр: SOURCE_DESCRIPTION содержит «Recon из b24.xlsx».
Новый SOURCE_ID резолвится из crm.status.list по NAME=«База Яндекс».

    docker compose exec bot python scripts/b24_lead_set_source.py            # реальный апдейт
    docker compose exec bot python scripts/b24_lead_set_source.py --dry-run  # только подсчёт
"""
import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvis")

from app.services.bitrix_client import BitrixClient  # noqa: E402

SOURCE_NAME = "База Яндекс"


async def _find_source_id(bx, name: str) -> str | None:
    r = await bx._request("crm.status.list", {"filter": {"ENTITY_ID": "SOURCE"}})
    name_low = name.strip().lower()
    for row in r.get("result", []) or []:
        if (row.get("NAME") or "").strip().lower() == name_low:
            return row.get("STATUS_ID")
    return None


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="не апдейтить, только показать сколько найдено")
    args = ap.parse_args()

    bx = BitrixClient()
    try:
        source_id = await _find_source_id(bx, SOURCE_NAME)
        if not source_id:
            sys.exit(f"❌ В B24 нет источника «{SOURCE_NAME}»")
        print(f"Источник «{SOURCE_NAME}» → STATUS_ID={source_id}")

        # Тянем все наши лиды
        all_leads: list[dict] = []
        start = 0
        while True:
            r = await bx._request("crm.lead.list", {
                "filter": {"%SOURCE_DESCRIPTION": "Recon из b24.xlsx"},
                "select": ["ID", "TITLE", "SOURCE_ID"],
                "start": start,
            })
            batch = r.get("result", []) or []
            all_leads.extend(batch)
            nxt = r.get("next")
            if nxt is None or not batch:
                break
            start = int(nxt)
        print(f"Найдено лидов: {len(all_leads)}")

        # Фильтруем — обновляем только те, у которых SOURCE_ID != source_id
        to_update = [
            ld for ld in all_leads if (ld.get("SOURCE_ID") or "") != source_id
        ]
        already = len(all_leads) - len(to_update)
        print(f"Уже с правильным источником: {already}")
        print(f"К обновлению: {len(to_update)}")

        if args.dry_run:
            print("[DRY-RUN] апдейт пропущен")
            return

        ok = 0
        fail = 0
        for i, ld in enumerate(to_update, 1):
            lid = ld.get("ID")
            try:
                await bx._request("crm.lead.update", {
                    "id": lid,
                    "fields": {"SOURCE_ID": source_id},
                })
                ok += 1
            except Exception as e:
                fail += 1
                logger.error("update %s failed: %s", lid, e)
            if i % 25 == 0:
                print(f"  ...прогресс {i}/{len(to_update)}: ok={ok} fail={fail}")
        print(f"\n=== ИТОГ === ok={ok} fail={fail} из {len(to_update)}")
    finally:
        await bx.close()


asyncio.run(main())
