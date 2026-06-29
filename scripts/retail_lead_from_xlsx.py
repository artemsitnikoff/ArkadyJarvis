#!/usr/bin/env python3
"""Разовый: берёт контакты с выставки из retail.xlsx (лист «Sheet1») и для каждого:
1. Запускает Claude CLI с WebFetch+WebSearch → recon-карточка КОМПАНИИ (JSON)
2. Создаёт лид в Bitrix24 на Сергея Добрякова (bitrix_id=3833) с этой инфой

Отличие от b24_lead_from_xlsx.py: тут каждая строка — это КОНКРЕТНЫЙ человек
(ФИО, должность, телефон, email, Telegram), пойманный на стенде. Контактные поля
лида берём ПРЯМО из файла, а разведку Claude'а используем только чтобы обогатить
профиль КОМПАНИИ (сайта в файле нет — определяем по домену корп. почты / WebSearch).
Лид создаём ВСЕГДА, даже если разведка пустая, — контакт с выставки уже ценен.

    # Тест по первым 5 строкам (реальное создание лидов):
    python scripts/retail_lead_from_xlsx.py --all --limit 5

    # Все строки:
    python scripts/retail_lead_from_xlsx.py --all

    # Без создания лида (dry-run, recon всё равно гоняется):
    python scripts/retail_lead_from_xlsx.py --all --limit 5 --dry-run

    # Переобработать уже сделанные (игнор чекпоинта):
    python scripts/retail_lead_from_xlsx.py --all --force
"""
import argparse
import asyncio
import json
import logging
import os
import re
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

from app.config import settings  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.bitrix_client import BitrixClient  # noqa: E402
from app.services.prompts import load_prompt  # noqa: E402
from app.utils import parse_json_response  # noqa: E402

# Сергей Добряков — региональный менеджер по продажам (резолвлен через user.get)
DOBRYAKOV_BITRIX_ID = 3833

# Значение в селекте «Источник» (CRM Lead). Резолвим по имени → STATUS_ID,
# fallback на хардкод UC_3NJ3TZ если не нашли.
LEAD_SOURCE_NAME = "С мероприятия"
LEAD_SOURCE_ID_FALLBACK = "UC_3NJ3TZ"
SOURCE_DESCRIPTION = "Выставка (retail.xlsx)"

XLSX_PATH = "retail.xlsx"
SHEET = "Sheet1"

# В data/ — volume-mounted, переживает docker compose up --build.
STATE_FILE = Path("data/retail_processed.json")

# Бесплатные почтовые провайдеры — их домен НЕ является сайтом компании.
FREE_EMAIL_DOMAINS = {
    "gmail.com", "googlemail.com", "yandex.ru", "ya.ru", "yandex.com",
    "mail.ru", "bk.ru", "list.ru", "inbox.ru", "internet.ru",
    "rambler.ru", "icloud.com", "me.com", "outlook.com", "hotmail.com",
    "live.com", "yahoo.com", "proton.me", "protonmail.com",
}


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
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _cell(v) -> str:
    return str(v).strip() if v is not None else ""


def _load_xlsx(path: str) -> list[dict]:
    """Список контактов: last, first, company, position, email, phone, tg."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb[SHEET]
    out: list[dict] = []
    for i, row in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue  # header
        row = (row + (None,) * 7)[:7]
        last, first, company, position, email, phone, tg = row
        if not any((last, first, company, email)):
            continue  # пустая строка
        out.append({
            "last": _cell(last),
            "first": _cell(first),
            "company": _cell(company),
            "position": _cell(position),
            "email": _cell(email),
            "phone": _cell(phone),
            "tg": _cell(tg),
        })
    return out


def _state_key(c: dict) -> str:
    """Уникальный ключ строки для чекпоинта."""
    if c.get("email"):
        return c["email"].lower()
    return f"{c.get('company','')}|{c.get('last','')}|{c.get('first','')}".lower()


def _strip_emoji(s: str) -> str:
    """Bitrix CRM COMMENTS — MySQL utf8 (3-байтовый). Emoji (U+10000+) обрезают
    строку. Чистим всё за пределы BMP."""
    return "".join(c for c in s if ord(c) <= 0xFFFF)


def _norm_phone(raw: str) -> str | None:
    """Нормализация телефона из выставочного файла к +7…/+375… формату."""
    digits = re.sub(r"\D", "", raw or "")
    if not digits:
        return None
    if len(digits) == 11 and digits[0] == "8":
        return "+7" + digits[1:]
    if len(digits) == 11 and digits[0] == "7":
        return "+" + digits
    if len(digits) == 10 and digits[0] == "9":
        return "+7" + digits
    if digits.startswith("375"):       # Беларусь
        return "+" + digits
    if len(digits) >= 11:
        return "+" + digits
    return raw.strip() or None          # как есть — пусть менеджер поправит


def _norm_tg(raw: str) -> tuple[str | None, str | None]:
    """(@handle, raw_для_комментария). Если значение не похоже на валидный
    Telegram-username (есть пробел / не-латиница) — handle=None, кладём raw в коммент."""
    s = (raw or "").strip().lstrip("@")
    if not s:
        return None, None
    if re.fullmatch(r"[A-Za-z0-9_]{4,32}", s):
        return "@" + s, None
    return None, raw.strip()           # напр. «Lyibov Milshteyn» — это имя, не ник


def _site_hint(email: str) -> str:
    """Домен корпоративной почты как подсказка по сайту. '' для бесплатных провайдеров."""
    if "@" not in (email or ""):
        return ""
    domain = email.split("@", 1)[1].strip().lower()
    if not domain or domain in FREE_EMAIL_DOMAINS:
        return ""
    return domain


async def _recon_searchonly(c: dict, ai: AIClient) -> dict:
    company, person = c["company"], _person(c)
    prompt = (
        load_prompt("retail_lead_recon_searchonly")
        .replace("{company}", company)
        .replace("{person}", person)
        .replace("{site_hint}", _site_hint(c["email"]) or "—")
    )
    logger.info("→ recon_searchonly %s", company)
    try:
        resp = await ai.complete(prompt, timeout=90, allowed_tools="WebSearch")
    except Exception as e:
        logger.warning("searchonly %s failed: %s", company, e)
        return {"site_unreachable": True, "error_reason": str(e)[:200]}
    try:
        data = parse_json_response(resp) or {}
    except Exception as e:
        return {"site_unreachable": True, "error_reason": f"invalid JSON: {e}"}
    data["site_unreachable"] = True
    return data


async def _recon_one(c: dict, ai: AIClient) -> dict:
    company, person = c["company"], _person(c)
    prompt = (
        load_prompt("retail_lead_recon")
        .replace("{company}", company)
        .replace("{person}", person)
        .replace("{site_hint}", _site_hint(c["email"]) or "—")
    )
    logger.info("→ recon %s", company)
    try:
        resp = await ai.complete(
            prompt, timeout=180, allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.warning("recon %s failed: %s — fallback WebSearch-only", company, e)
        return await _recon_searchonly(c, ai)
    try:
        data = parse_json_response(resp) or {}
    except Exception:
        logger.warning("recon %s: invalid JSON — fallback WebSearch-only", company)
        return await _recon_searchonly(c, ai)
    if not data or data.get("error"):
        return await _recon_searchonly(c, ai)
    return data


def _person(c: dict) -> str:
    return " ".join(p for p in (c.get("first"), c.get("last")) if p).strip()


def _website(c: dict, recon: dict) -> str | None:
    """Сайт: из recon, иначе из подсказки корп. почты."""
    w = (recon.get("website") or "").strip() if recon else ""
    if w:
        w = w.replace("https://", "").replace("http://", "").strip("/ ")
        return w or None
    return _site_hint(c["email"]) or None


def _build_short_summary(c: dict, recon: dict) -> str:
    """Короткая шапка для COMMENTS (видна в канбане)."""
    bits = [f"Контакт с выставки: {_person(c)}" + (f", {c['position']}" if c["position"] else "") + "."]
    if recon.get("site_unreachable"):
        bits.append("[Сайт не открылся — данные из WebSearch]")
    if recon.get("industry"):
        bits.append(f"Отрасль: {recon['industry']}.")
    if recon.get("region"):
        bits.append(f"Регион: {recon['region']}.")
    if not (recon.get("industry") or recon.get("what_they_do")):
        bits.append("[Разведка не дала результатов.]")
    bits.append("Подробно → в таймлайне справа.")
    return _strip_emoji(" ".join(bits))


def _build_timeline_comment(c: dict, recon: dict) -> str:
    """Полная карточка для timeline.comment (BBCODE)."""
    sections: list[str] = []

    # === Контакт с выставки (из файла) ===
    _, tg_raw = _norm_tg(c["tg"])
    contact = [
        "[B]=== КОНТАКТ С ВЫСТАВКИ ===[/B]",
        f"- ФИО: {_person(c)}",
    ]
    if c["position"]:
        contact.append(f"- Должность: {c['position']}")
    if c["company"]:
        contact.append(f"- Компания: {c['company']}")
    if c["phone"]:
        contact.append(f"- Телефон: {_norm_phone(c['phone']) or c['phone']}")
    if c["email"]:
        contact.append(f"- Email: {c['email']}")
    if c["tg"]:
        tg_handle, _ = _norm_tg(c["tg"])
        contact.append(f"- Telegram: {tg_handle or c['tg']}")
    sections.append("\n".join(contact))

    if recon.get("site_unreachable"):
        warn = [
            "[B]=== ⚠ САЙТ НЕ ОТКРЫЛСЯ ===[/B]",
            "Контент сайта получить не удалось (Cloudflare / антибот / 5xx / таймаут / сайт не найден).",
            "Данные ниже — из WebSearch, могут быть неполными.",
        ]
        if recon.get("error_reason"):
            warn.append(f"Причина: {recon['error_reason']}")
        sections.append(_strip_emoji("\n".join(warn)))

    # === Профиль компании ===
    site = _website(c, recon)
    head = ["[B]=== ПРОФИЛЬ КОМПАНИИ ===[/B]"]
    if site:
        head.append(f"- Сайт: https://{site}")
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
    if len(head) == 1:
        head.append("- (разведка не дала данных по компании)")
    sections.append("\n".join(head))

    # === Контакты компании (из открытых источников) ===
    kc = recon.get("key_contacts") or []
    phones = recon.get("phones") or []
    emails = recon.get("emails") or []
    if kc or phones or emails:
        cb = ["[B]=== КОНТАКТЫ КОМПАНИИ (открытые источники) ===[/B]"]
        for k in kc:
            cb.append(f"- {k}")
        if phones:
            cb.append("Тел: " + ", ".join(phones))
        if emails:
            cb.append("Email: " + ", ".join(emails))
        sections.append("\n".join(cb))

    # === Соцсети ===
    social = {k: v for k, v in (recon.get("social") or {}).items() if v}
    if social:
        sb = ["[B]=== СОЦСЕТИ / МЕССЕНДЖЕРЫ ===[/B]"]
        for k, v in social.items():
            sb.append(f"- {k}: {v}")
        sections.append("\n".join(sb))

    # === Новости ===
    news = recon.get("news_hooks") or []
    if news:
        nb = ["[B]=== НОВОСТИ И ИНФОПОВОДЫ ===[/B]"]
        nb += [f"- {n}" for n in news]
        sections.append("\n".join(nb))

    # === Анализ рынка ===
    idyn = recon.get("industry_dynamics")
    iresearch = recon.get("industry_research") or []
    cdyn = recon.get("competitor_count_dynamics")
    if idyn or iresearch or cdyn:
        mb = ["[B]=== АНАЛИЗ РЫНКА ===[/B]"]
        if idyn:
            mb.append(f"Динамика индустрии: {idyn}")
        if cdyn:
            mb.append(f"Число игроков: {cdyn}")
        if iresearch:
            mb.append("\nИсследования:")
            for r in iresearch:
                title, url, kt = r.get("title", ""), r.get("url", ""), r.get("key_takeaway", "")
                mb.append(f"- {title}{(' — ' + kt) if kt else ''}")
                if url:
                    mb.append(f"  {url}")
        sections.append("\n".join(mb))

    # === Топ конкурентов ===
    top = recon.get("top_competitors") or []
    if top:
        tc = ["[B]=== ТОП-3 КОНКУРЕНТА ===[/B]"]
        for comp in top[:3]:
            name, sc, why = comp.get("name", ""), comp.get("site", ""), comp.get("why_competitor", "")
            line = f"- {name}" + (f" ({sc})" if sc else "") + (f" — {why}" if why else "")
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


async def _resolve_source_id(bitrix, name: str) -> str | None:
    r = await bitrix._request("crm.status.list", {"filter": {"ENTITY_ID": "SOURCE"}})
    name_low = name.strip().lower()
    for row in (r.get("result") or []):
        if (row.get("NAME") or "").strip().lower() == name_low:
            return row.get("STATUS_ID")
    return None


def _build_lead_fields(c: dict, recon: dict, source_id: str) -> dict:
    company = c["company"] or recon.get("company_name") or "—"
    person = _person(c)
    title = f"{company} — {person}" if person else company

    fields: dict = {
        "TITLE": title,
        "NAME": c["first"] or None,
        "LAST_NAME": c["last"] or None,
        "COMPANY_TITLE": company,
        "POST": c["position"] or None,
        "COMMENTS": _build_short_summary(c, recon),
        "ASSIGNED_BY_ID": DOBRYAKOV_BITRIX_ID,
        "STATUS_ID": "NEW",
        "SOURCE_ID": source_id,
        "SOURCE_DESCRIPTION": SOURCE_DESCRIPTION,
    }
    fields = {k: v for k, v in fields.items() if v is not None}

    phone = _norm_phone(c["phone"])
    if phone:
        fields["PHONE"] = [{"VALUE": phone, "VALUE_TYPE": "WORK"}]
    if c["email"]:
        fields["EMAIL"] = [{"VALUE": c["email"], "VALUE_TYPE": "WORK"}]

    site = _website(c, recon)
    if site:
        fields["WEB"] = [{"VALUE": f"https://{site}", "VALUE_TYPE": "WORK"}]

    tg_handle, _ = _norm_tg(c["tg"])
    if tg_handle:
        fields["IM"] = [{"VALUE": tg_handle, "VALUE_TYPE": "TELEGRAM"}]

    return fields


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--all", action="store_true", help="по всем строкам файла")
    g.add_argument("--row", type=int, help="одна строка по индексу (с 1)")
    ap.add_argument("--limit", type=int, default=0, help="макс. строк при --all (0 = без лимита)")
    ap.add_argument("--dry-run", action="store_true", help="не создавать лида в B24")
    ap.add_argument("--force", action="store_true", help="игнорировать чекпоинт")
    ap.add_argument("--recon-file", help="JSON {state_key: recon} — готовая разведка "
                    "вместо вызова Claude (нужно если WebSearch недоступен локально)")
    args = ap.parse_args()

    if not Path(XLSX_PATH).exists():
        raise FileNotFoundError(f"{XLSX_PATH} не найден в корне")
    contacts = _load_xlsx(XLSX_PATH)
    logger.info("Загрузил %d контактов из %s", len(contacts), XLSX_PATH)

    if args.row:
        contacts = contacts[args.row - 1: args.row]
    elif args.limit:
        contacts = contacts[: args.limit]

    recon_override: dict = {}
    if args.recon_file:
        try:
            recon_override = json.loads(Path(args.recon_file).read_text(encoding="utf-8"))
            logger.info("recon-override: %d записей из %s", len(recon_override), args.recon_file)
        except Exception as e:
            logger.warning("Не смог прочитать --recon-file %s: %s", args.recon_file, e)

    state = _load_state()
    DONE = {"ok", "skip", "error"}
    pending = [
        c for c in contacts
        if args.force or state.get(_state_key(c), {}).get("status") not in DONE
    ]
    logger.info(
        "К обработке %d, уже сделано %d", len(pending), len(contacts) - len(pending),
    )

    ai = AIClient()
    bitrix = BitrixClient()
    stats = {"ok": 0, "err": 0}
    portal = settings.bitrix_domain
    try:
        source_id = await _resolve_source_id(bitrix, LEAD_SOURCE_NAME) or LEAD_SOURCE_ID_FALLBACK
        logger.info("Lead SOURCE_ID «%s» → %s", LEAD_SOURCE_NAME, source_id)

        for idx, c in enumerate(pending, start=1):
            key = _state_key(c)
            print(f"\n[{idx}/{len(pending)}] === {c['company']} / {_person(c)} ===")
            if key in recon_override:
                recon = recon_override[key] or {}
                logger.info("recon из override для %s", c["company"])
            else:
                recon = await _recon_one(c, ai)
            fields = _build_lead_fields(c, recon, source_id)
            if args.dry_run:
                print(f"[DRY-RUN] поля лида:\n{json.dumps(fields, ensure_ascii=False, indent=2)}")
                continue
            try:
                res = await bitrix.create_lead(fields)
                lead_id = res.get("id")
                cid = None
                if lead_id:
                    timeline = _build_timeline_comment(c, recon)
                    try:
                        cid = await bitrix.add_timeline_comment(lead_id, "lead", timeline)
                    except Exception as e:
                        logger.error("timeline %s failed: %s", c["company"], e)
                stats["ok"] += 1
                state[key] = {
                    "status": "ok", "lead_id": lead_id, "timeline_id": cid,
                    "company": c["company"], "person": _person(c),
                    "site_unreachable": bool(recon.get("site_unreachable")),
                    "ts": datetime.now().isoformat(),
                }
                _save_state(state)
                url = f"https://{portal}/crm/lead/details/{lead_id}/"
                unr = " [⚠ site_unreachable]" if recon.get("site_unreachable") else ""
                print(f"✓ lead={lead_id}{unr}  {url}")
            except Exception as e:
                stats["err"] += 1
                logger.error("create_lead %s failed: %s", c["company"], e)
                state[key] = {
                    "status": "error", "error": str(e)[:200],
                    "company": c["company"], "ts": datetime.now().isoformat(),
                }
                _save_state(state)
    finally:
        await bitrix.close()
        print(
            f"\n=== ИТОГ === ok={stats['ok']} err={stats['err']} state={STATE_FILE}"
        )


asyncio.run(main())
