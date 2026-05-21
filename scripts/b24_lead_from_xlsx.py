#!/usr/bin/env python3
"""Разовый: берёт домены из b24.xlsx (лист «Клиенты») и для каждого:
1. Запускает Claude CLI с WebFetch+WebSearch → собирает recon-карточку (JSON)
2. Создаёт лид в Bitrix24 на Костю Карачева (bitrix_id=697) с этой инфой

    # Тестовый прогон по 3 заранее указанным доменам:
    python scripts/b24_lead_from_xlsx.py --test

    # По всем строкам листа «Клиенты» (берёт колонку «КА»):
    python scripts/b24_lead_from_xlsx.py --all --limit 10

    # Без реального создания лида (dry-run):
    python scripts/b24_lead_from_xlsx.py --test --dry-run
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvis")

import openpyxl  # noqa: E402

from app.services.ai_client import AIClient  # noqa: E402
from app.services.bitrix_client import BitrixClient  # noqa: E402
from app.services.prompts import load_prompt  # noqa: E402
from app.utils import parse_json_response  # noqa: E402

# Костя Карачев — bitrix_user_id из CLAUDE.md
KOSTYA_BITRIX_ID = 697

TEST_DOMAINS = [
    "dmitriev-ivan.ru",
    "gtkbt.ru",
    "energostan.ru",
]


def _read_domains_from_xlsx(path: str, limit: int) -> list[str]:
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Клиенты"]
    domains: list[str] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        ka = (row[0] or "").strip() if row[0] else ""
        if not ka:
            continue
        # Берём только то что выглядит как домен (есть точка)
        if "." in ka and " " not in ka:
            domains.append(ka.lower())
            if len(domains) >= limit:
                break
    return domains


async def _recon_one(site: str, ai: AIClient) -> dict:
    """Запуск Claude с WebFetch+WebSearch — возвращает recon JSON."""
    prompt = load_prompt("b24_lead_recon").replace("{site}", site)
    logger.info("→ recon %s", site)
    try:
        resp = await ai.complete(
            prompt,
            timeout=300,  # CL может долго ходить по сайту
            allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.error("recon %s failed: %s", site, e)
        return {"error": str(e)}
    data = parse_json_response(resp) or {}
    if not data:
        data = {"error": "не смог распарсить JSON", "raw": resp[:500]}
    return data


def _build_lead_fields(site: str, recon: dict) -> dict:
    """Превращает recon-JSON в поля для crm.lead.add."""
    company = recon.get("company_name") or site
    title = f"{company} ({site})"

    lines: list[str] = [f"🌐 Сайт: https://{site}"]
    if recon.get("industry"):
        lines.append(f"🏷 Отрасль: {recon['industry']}")
    if recon.get("region"):
        lines.append(f"📍 Регион: {recon['region']}")
    if recon.get("what_they_do"):
        lines.append(f"\n🔧 Чем занимается:\n{recon['what_they_do']}")
    if recon.get("target_audience"):
        lines.append(f"\n🎯 Аудитория: {recon['target_audience']}")
    if recon.get("size_hint"):
        lines.append(f"📏 Масштаб: {recon['size_hint']}")
    kc = recon.get("key_contacts") or []
    if kc:
        lines.append("\n👤 Контакты:")
        for k in kc:
            lines.append(f"  • {k}")
    phones = recon.get("phones") or []
    if phones:
        lines.append("📞 Телефоны: " + ", ".join(phones))
    emails = recon.get("emails") or []
    if emails:
        lines.append("✉️ Email: " + ", ".join(emails))
    social = recon.get("social") or {}
    if any(social.values()):
        lines.append("🔗 Соцсети: " + " · ".join(
            f"{k}: {v}" for k, v in social.items() if v
        ))
    news = recon.get("news_hooks") or []
    if news:
        lines.append("\n📰 Новости / зацепки:")
        for n in news:
            lines.append(f"  • {n}")
    if recon.get("pain_points_hypothesis"):
        lines.append(f"\n💡 Возможные боли: {recon['pain_points_hypothesis']}")
    if recon.get("why_dc_can_help"):
        lines.append(f"\n🚀 Чем DC может помочь: {recon['why_dc_can_help']}")

    fields: dict = {
        "TITLE": title,
        "NAME": company,
        "COMMENTS": "\n".join(lines),
        "ASSIGNED_BY_ID": KOSTYA_BITRIX_ID,
        "STATUS_ID": "NEW",
        "SOURCE_ID": "OTHER",
        "SOURCE_DESCRIPTION": "Хадсон-recon (b24.xlsx)",
        "WEB": [{"VALUE": f"https://{site}", "VALUE_TYPE": "WORK"}],
    }
    if phones:
        fields["PHONE"] = [{"VALUE": p, "VALUE_TYPE": "WORK"} for p in phones[:3]]
    if emails:
        fields["EMAIL"] = [{"VALUE": e, "VALUE_TYPE": "WORK"} for e in emails[:3]]
    return fields


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true", help="прогон по 3 тестовым доменам")
    g.add_argument("--all", action="store_true", help="по листу «Клиенты»")
    ap.add_argument("--limit", type=int, default=5, help="макс. кол-во строк при --all")
    ap.add_argument("--dry-run", action="store_true", help="не создавать лида в B24")
    args = ap.parse_args()

    if args.test:
        domains = TEST_DOMAINS
    else:
        path = "b24.xlsx"
        if not Path(path).exists():
            raise FileNotFoundError(f"{path} не найден в корне")
        domains = _read_domains_from_xlsx(path, limit=args.limit)
    logger.info("Будем обрабатывать %d доменов", len(domains))

    ai = AIClient()
    bitrix = BitrixClient()
    try:
        for site in domains:
            recon = await _recon_one(site, ai)
            print(f"\n=== {site} ===")
            print(json.dumps(recon, ensure_ascii=False, indent=2))
            if recon.get("error"):
                logger.warning("Skip %s: %s", site, recon["error"])
                continue
            fields = _build_lead_fields(site, recon)
            if args.dry_run:
                print(f"[DRY-RUN] lead fields:")
                print(json.dumps(fields, ensure_ascii=False, indent=2)[:1500])
                continue
            try:
                res = await bitrix.create_lead(fields)
                print(f"✓ Лид создан: id={res.get('id')}")
            except Exception as e:
                logger.error("Bitrix create_lead %s failed: %s", site, e)
    finally:
        await bitrix.close()


asyncio.run(main())
