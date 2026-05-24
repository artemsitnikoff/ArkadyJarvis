#!/usr/bin/env python3
"""Восстанавливает b24_processed.json из реально созданных лидов в B24.

Тянет все crm.lead.list где SOURCE_DESCRIPTION содержит 'Recon из b24.xlsx',
парсит домен из WEB[0] или из конца TITLE ('Company (domain.ru)') и
проставляет каждому в state status=ok с реальным lead_id.

Запускать после того как баг с STATE_FILE починен (state теперь в data/).

    docker compose exec bot python scripts/rebuild_b24_state.py
"""
import asyncio
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvis")

from app.services.bitrix_client import BitrixClient  # noqa: E402

STATE_FILE = Path("data/b24_processed.json")

# Парсер домена из TITLE: «...Company (domain.ru)»
TITLE_DOMAIN_RE = re.compile(r"\(([a-zA-Z0-9.\-_]+\.[a-zA-Zа-яА-Я]+)\)\s*$")


async def main() -> None:
    bx = BitrixClient()
    all_leads: list[dict] = []
    try:
        start = 0
        while True:
            r = await bx._request(
                "crm.lead.list",
                {
                    "filter": {"%SOURCE_DESCRIPTION": "Recon из b24.xlsx"},
                    "select": ["ID", "TITLE", "DATE_CREATE", "WEB",
                               "SOURCE_DESCRIPTION"],
                    "start": start,
                },
            )
            batch = r.get("result", []) or []
            all_leads.extend(batch)
            nxt = r.get("next")
            if nxt is None or not batch:
                break
            start = int(nxt)
    finally:
        await bx.close()

    logger.info("Получено %d лидов от b24-скрипта", len(all_leads))

    # Загружаем что есть, объединяем с реальностью из B24
    if STATE_FILE.exists():
        state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    else:
        # пробуем legacy
        legacy = Path("b24_processed.json")
        state = json.loads(legacy.read_text(encoding="utf-8")) if legacy.exists() else {}

    before_ok = sum(1 for v in state.values() if v.get("status") == "ok")
    added = 0
    for lead in all_leads:
        lid = lead.get("ID")
        title = (lead.get("TITLE") or "").strip()
        web = lead.get("WEB") or []

        # 1) Сайт из поля WEB
        site = None
        for w in web:
            v = (w.get("VALUE") or "").strip()
            if v:
                v = v.replace("https://", "").replace("http://", "").strip("/")
                site = v.lower()
                break

        # 2) Fallback: из конца TITLE
        if not site:
            m = TITLE_DOMAIN_RE.search(title)
            if m:
                site = m.group(1).lower()

        if not site:
            logger.warning("не смог извлечь домен из лида %s: %s", lid, title[:80])
            continue

        cur = state.get(site, {})
        if cur.get("status") == "ok" and cur.get("lead_id"):
            continue  # уже есть в state — не трогаем
        state[site] = {
            "status": "ok",
            "lead_id": int(lid) if lid else None,
            "timeline_id": cur.get("timeline_id"),
            "site_unreachable": cur.get("site_unreachable", False),
            "ts": lead.get("DATE_CREATE") or datetime.now().isoformat(),
            "rebuilt_from_b24": True,
        }
        added += 1

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
    )

    after_ok = sum(1 for v in state.values() if v.get("status") == "ok")
    print(f"\nДобавлено в state из B24: {added}")
    print(f"Было ok={before_ok}, стало ok={after_ok}, всего записей={len(state)}")
    print(f"State: {STATE_FILE}")


asyncio.run(main())
