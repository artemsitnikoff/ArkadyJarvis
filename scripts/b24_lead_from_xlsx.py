#!/usr/bin/env python3
"""Разовый: берёт домены из b24.xlsx (лист «Клиенты») и для каждого:
1. Запускает Claude CLI с WebFetch+WebSearch → собирает recon-карточку (JSON)
2. Создаёт лид в Bitrix24 на Костю Карачева (bitrix_id=697) с этой инфой

    # Тестовый прогон по 3 заранее указанным доменам:
    python scripts/b24_lead_from_xlsx.py --test

    # По всем строкам листа «Клиенты» (берёт колонку «КА»):
    python scripts/b24_lead_from_xlsx.py --all --limit 10

    # Один конкретный домен:
    python scripts/b24_lead_from_xlsx.py --site energostan.ru

    # Без реального создания лида (dry-run):
    python scripts/b24_lead_from_xlsx.py --test --dry-run
"""
import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
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

STATE_FILE = Path("b24_processed.json")  # в корне репо — гит-трекаемое



def _load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _load_xlsx(path: str) -> dict[str, dict]:
    """{domain: {subindustry, agency, period, revenue, revenue_prev, pop, revenue_year_ago}}."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb["Клиенты"]
    out: dict[str, dict] = {}
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        ka = row[0]
        if not ka:
            continue
        ka_str = str(ka).strip()
        if "." not in ka_str or " " in ka_str:
            continue
        out[ka_str.lower()] = {
            "subindustry": row[1],
            "agency": row[2],
            "period": row[3],
            "revenue": row[4],
            "revenue_prev": row[5],
            "pop": row[6],
            "revenue_year_ago": row[7] if len(row) > 7 else None,
        }
    return out


def _strip_emoji(s: str) -> str:
    """Bitrix CRM COMMENTS — MySQL utf8 (3-байтовый). Emoji (supplementary
    plane U+10000+) обрезают всю строку. Чистим всё за пределы BMP."""
    return "".join(c for c in s if ord(c) <= 0xFFFF)


def _fmt_money(v) -> str:
    try:
        return f"{float(v):,.0f} ₽".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


def _fmt_pop(v) -> str:
    try:
        n = float(v)
        # PoP в xlsx бывает и в % (например, 77) и в долях (0.77) — проверим
        if abs(n) < 5:
            n *= 100
        sign = "+" if n >= 0 else ""
        return f"{sign}{n:.0f}%"
    except (TypeError, ValueError):
        return "—"


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


def _build_short_summary(site: str, recon: dict, fin: dict | None) -> str:
    """Короткая шапка для COMMENTS (видна в канбане). 2-4 строки."""
    bits = []
    if fin:
        bits.append(
            f"Выручка {fin.get('period') or '-'}: {_fmt_money(fin.get('revenue'))} "
            f"(PoP {_fmt_pop(fin.get('pop'))}). Агентство: {fin.get('agency') or '-'}."
        )
    if recon.get("industry"):
        bits.append(f"Отрасль: {recon['industry']}.")
    if recon.get("region"):
        bits.append(f"Регион: {recon['region']}.")
    bits.append("Подробный recon → в таймлайне справа.")
    return _strip_emoji(" ".join(bits))


def _build_timeline_comment(site: str, recon: dict, fin: dict | None) -> str:
    """Полная карточка recon для timeline.comment (BBCODE)."""
    sections: list[str] = []

    # === Финансы (из b24.xlsx) ===
    if fin:
        fin_lines = [
            "[B]=== ФИНАНСЫ (из b24.xlsx) ===[/B]",
            f"- Период: {fin.get('period') or '-'}",
            f"- Текущая выручка: [B]{_fmt_money(fin.get('revenue'))}[/B]",
            f"- Прошлый период: {_fmt_money(fin.get('revenue_prev'))}  (PoP: {_fmt_pop(fin.get('pop'))})",
            f"- Год назад: {_fmt_money(fin.get('revenue_year_ago'))}",
            f"- Subindustry: {fin.get('subindustry') or '-'}",
            f"- Текущее агентство: {fin.get('agency') or '-'}",
        ]
        sections.append("\n".join(fin_lines))

    # === Профиль компании ===
    head: list[str] = [
        "[B]=== ПРОФИЛЬ КОМПАНИИ ===[/B]",
        f"- Сайт: https://{site}",
    ]
    if recon.get("industry"):
        head.append(f"- Отрасль: {recon['industry']}")
    if recon.get("region"):
        head.append(f"- Регион: {recon['region']}")
    if recon.get("size_hint"):
        head.append(f"- Масштаб: {recon['size_hint']}")
    if recon.get("target_audience"):
        head.append(f"- Аудитория: {recon['target_audience']}")
    if recon.get("what_they_do"):
        head.append(f"\nЧем занимаются:\n{recon['what_they_do']}")
    sections.append("\n".join(head))

    # === Контакты ===
    kc = recon.get("key_contacts") or []
    phones = recon.get("phones") or []
    emails = recon.get("emails") or []
    if kc or phones or emails:
        cb = ["[B]=== КОНТАКТЫ ===[/B]"]
        for k in kc:
            cb.append(f"- {k}")
        if phones:
            cb.append("Тел: " + ", ".join(phones))
        if emails:
            cb.append("Email: " + ", ".join(emails))
        sections.append("\n".join(cb))

    # === Соцсети / мессенджеры ===
    social = recon.get("social") or {}
    social_present = {k: v for k, v in social.items() if v}
    if social_present:
        sb = ["[B]=== СОЦСЕТИ / МЕССЕНДЖЕРЫ ===[/B]"]
        for k, v in social_present.items():
            sb.append(f"- {k}: {v}")
        sections.append("\n".join(sb))

    # === Новости и зацепки ===
    news = recon.get("news_hooks") or []
    if news:
        nb = ["[B]=== НОВОСТИ И ИНФОПОВОДЫ ===[/B]"]
        for n in news:
            nb.append(f"- {n}")
        sections.append("\n".join(nb))

    # === Анализ рынка ===
    industry_dynamics = recon.get("industry_dynamics")
    industry_research = recon.get("industry_research") or []
    competitor_dyn = recon.get("competitor_count_dynamics")
    if industry_dynamics or industry_research or competitor_dyn:
        mb = ["[B]=== АНАЛИЗ РЫНКА ===[/B]"]
        if industry_dynamics:
            mb.append(f"Динамика индустрии: {industry_dynamics}")
        if competitor_dyn:
            mb.append(f"Число игроков: {competitor_dyn}")
        if industry_research:
            mb.append("\nИсследования:")
            for r in industry_research:
                title = r.get("title", "")
                url = r.get("url", "")
                kt = r.get("key_takeaway", "")
                mb.append(f"- {title}{(' — ' + kt) if kt else ''}")
                if url:
                    mb.append(f"  {url}")
        sections.append("\n".join(mb))

    # === Топ конкурентов ===
    top_comp = recon.get("top_competitors") or []
    if top_comp:
        tc = ["[B]=== ТОП-3 КОНКУРЕНТА ===[/B]"]
        for c in top_comp[:3]:
            name = c.get("name", "")
            site_c = c.get("site", "")
            why = c.get("why_competitor", "")
            line = f"- {name}"
            if site_c:
                line += f" ({site_c})"
            if why:
                line += f" — {why}"
            tc.append(line)
        sections.append("\n".join(tc))

    # === Боли и план ===
    if recon.get("pain_points_hypothesis") or recon.get("why_dc_can_help"):
        pb = ["[B]=== ГИПОТЕЗА: БОЛИ И ПОДХОД DC ===[/B]"]
        if recon.get("pain_points_hypothesis"):
            pb.append(f"Боли:\n{recon['pain_points_hypothesis']}")
        if recon.get("why_dc_can_help"):
            pb.append(f"\nЧем DC может помочь:\n{recon['why_dc_can_help']}")
        sections.append("\n".join(pb))

    return _strip_emoji("\n\n---\n\n".join(sections))


def _build_lead_fields(site: str, recon: dict, fin: dict | None = None) -> dict:
    """Поля для crm.lead.add. Полный recon идёт ОТДЕЛЬНО в timeline.comment.add."""
    company = recon.get("company_name") or site
    title = f"{company} ({site})"

    phones = recon.get("phones") or []
    emails = recon.get("emails") or []
    social = recon.get("social") or {}

    fields: dict = {
        "TITLE": title,
        "NAME": company,
        "COMMENTS": _build_short_summary(site, recon, fin),
        "ASSIGNED_BY_ID": KOSTYA_BITRIX_ID,
        "STATUS_ID": "NEW",
        "SOURCE_ID": "OTHER",
        "SOURCE_DESCRIPTION": f"Recon из b24.xlsx ({fin.get('period') if fin else '-'})",
        "WEB": [{"VALUE": f"https://{site}", "VALUE_TYPE": "WORK"}],
    }
    if phones:
        fields["PHONE"] = [{"VALUE": p, "VALUE_TYPE": "WORK"} for p in phones[:3]]
    if emails:
        fields["EMAIL"] = [{"VALUE": e, "VALUE_TYPE": "WORK"} for e in emails[:3]]

    im_list: list[dict] = []
    tg = social.get("telegram")
    if tg and isinstance(tg, str):
        im_list.append({"VALUE": tg, "VALUE_TYPE": "TELEGRAM"})
    wa = social.get("whatsapp")
    if wa and isinstance(wa, str):
        im_list.append({"VALUE": wa, "VALUE_TYPE": "WHATSAPP"})
    if im_list:
        fields["IM"] = im_list

    return fields


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--test", action="store_true", help="прогон по 3 тестовым доменам")
    g.add_argument("--all", action="store_true", help="по листу «Клиенты»")
    g.add_argument("--site", help="один конкретный домен (например energostan.ru)")
    ap.add_argument("--limit", type=int, default=0,
                    help="макс. кол-во строк при --all (0 = без лимита)")
    ap.add_argument("--dry-run", action="store_true", help="не создавать лида в B24")
    ap.add_argument("--force", action="store_true",
                    help="игнорировать чекпоинт — переобрабатывать всех")
    args = ap.parse_args()

    path = "b24.xlsx"
    if not Path(path).exists():
        raise FileNotFoundError(f"{path} не найден в корне")
    xlsx_data = _load_xlsx(path)
    logger.info("Загрузил %d записей из xlsx", len(xlsx_data))

    state = _load_state()
    if args.test:
        domains = TEST_DOMAINS
    elif args.site:
        domains = [args.site.strip().lower()]
    else:
        domains = list(xlsx_data.keys())
        if args.limit:
            domains = domains[: args.limit]

    pending = [
        d for d in domains
        if args.force or state.get(d, {}).get("status") != "ok"
    ]
    skipped_already_done = len(domains) - len(pending)
    logger.info(
        "Всего %d доменов, к обработке %d, уже сделано %d",
        len(domains), len(pending), skipped_already_done,
    )

    ai = AIClient()
    bitrix = BitrixClient()
    stats = {"ok": 0, "err": 0, "skip_recon": 0}
    try:
        for idx, site in enumerate(pending, start=1):
            print(f"\n[{idx}/{len(pending)}] === {site} ===")
            recon = await _recon_one(site, ai)
            if recon.get("error"):
                logger.warning("Skip %s: %s", site, recon["error"])
                stats["skip_recon"] += 1
                state[site] = {
                    "status": "skip_recon",
                    "error": str(recon.get("error"))[:200],
                    "ts": datetime.now().isoformat(),
                }
                _save_state(state)
                continue
            fin = xlsx_data.get(site)
            fields = _build_lead_fields(site, recon, fin)
            if args.dry_run:
                print(f"[DRY-RUN] {site} — recon ok, lead не создан")
                continue
            try:
                res = await bitrix.create_lead(fields)
                lead_id = res.get("id")
                cid = None
                if lead_id:
                    timeline = _build_timeline_comment(site, recon, fin)
                    try:
                        cid = await bitrix.add_timeline_comment(
                            lead_id, "lead", timeline,
                        )
                    except Exception as e:
                        logger.error("timeline %s failed: %s", site, e)
                stats["ok"] += 1
                state[site] = {
                    "status": "ok",
                    "lead_id": lead_id,
                    "timeline_id": cid,
                    "ts": datetime.now().isoformat(),
                }
                _save_state(state)
                print(
                    f"✓ {site}: lead={lead_id} timeline={cid} "
                    f"| итого ok={stats['ok']} err={stats['err']} skip={stats['skip_recon']}"
                )
            except Exception as e:
                stats["err"] += 1
                logger.error("create_lead %s failed: %s", site, e)
                state[site] = {
                    "status": "error",
                    "error": str(e)[:200],
                    "ts": datetime.now().isoformat(),
                }
                _save_state(state)
    finally:
        await bitrix.close()
        print(
            f"\n=== ИТОГ === ok={stats['ok']} err={stats['err']} "
            f"skip_recon={stats['skip_recon']} state={STATE_FILE}"
        )


asyncio.run(main())
