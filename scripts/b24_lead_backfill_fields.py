#!/usr/bin/env python3
"""Бэкфилл 5 пользовательских полей на уже залитых b24-recon лидах.

Без Claude, только Bitrix API + xlsx + парсинг существующего COMMENTS.

Заполняет:
  - Агентство Текущее    ← из b24.xlsx (колонка C «Агентство»)
  - Бюджет на рекламу    ← из b24.xlsx (выручка → bucket)
  - Город                ← из COMMENTS лида (parse «Регион: ...»)
  - Сфера деятельности   ← из COMMENTS («Отрасль: ...»)
  - Есть сотовый         ← из PHONE лида (есть ли мобильный +7 9XX)

Режимы:
    --discover                         # вывести найденные UF_* коды и их типы
    --industries                       # собрать уникальные «сферы» в industries.txt
    --dry-run [--limit N]              # показать что бы поставили на первые N
    (без флагов)                       # реальный апдейт всех b24-лидов
"""
import argparse
import asyncio
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("arkadyjarvis")

import openpyxl  # noqa: E402

from app.services.bitrix_client import BitrixClient  # noqa: E402


# --- Парсинг ---

COMMENTS_INDUSTRY_RE = re.compile(r"Отрасль:\s*([^.]+)\.")
COMMENTS_REGION_RE = re.compile(r"Регион:\s*([^.]+)\.")
TITLE_DOMAIN_RE = re.compile(r"\(([a-zA-Z0-9.\-_]+\.[a-zA-Zа-яА-Я]+)\)\s*$")

# Бакеты бюджета — порог «до» (исключительный) → label EXACTLY как в B24 enum
BUDGET_BUCKETS = [
    (500_000, "до 500.000"),
    (1_000_000, "500.000-1.000.000"),
    (3_000_000, "1.000.000-3.000.000"),
    (float("inf"), "Выше 3.000.000"),
]

# Кандидаты по titles для авто-поиска
FIELD_TARGETS = {
    "agency":     ["агентство текущее", "агентств"],
    "city":       ["город"],
    "industry":   ["сфера деятельности", "сфера"],
    "has_mobile": ["есть сотовый", "сотов"],
    "budget":     ["бюджет на рекламу", "бюджет"],
}

# Hard-coded коды (title в B24 пока = code, auto-match не находит).
# Перепроверь через --discover если что-то поменяется.
FIELD_OVERRIDES = {
    "agency":     "UF_CRM_1779947430020",   # string «Агентство Текущее»
    "city":       "ADDRESS_CITY",            # стандартное поле адреса
    "industry":   "UF_CRM_1779947512191",   # string «Сфера деятельности»
    "has_mobile": "UF_CRM_1779947540127",   # boolean «Есть сотовый»
    "budget":     "UF_CRM_1779947613495",   # enumeration «Бюджет на рекламу»
}


def bucket_budget(rev) -> str | None:
    try:
        v = float(rev)
    except (TypeError, ValueError):
        return None
    if v <= 0:
        return None
    for limit, label in BUDGET_BUCKETS:
        if v < limit:
            return label
    return BUDGET_BUCKETS[-1][1]


def extract_city(region: str | None) -> str | None:
    """«Челябинск, доставка по РФ» → «Челябинск». Также режет по скобкам."""
    if not region:
        return None
    # отрезаем по запятой / тире / скобке / точке-с-запятой
    head = re.split(r"[,;—\-\(]", region, maxsplit=1)[0]
    # снимем префиксы типа «Головной офис —»
    head = re.sub(
        r"^\s*(головной офис|офис|штаб[- ]квартира|город|г\.)\s*[:\-—]?\s*",
        "", head, flags=re.IGNORECASE,
    )
    return head.strip() or None


def has_mobile(phones: list) -> bool:
    """Любой +7 9XX или 8 9XX в списке телефонов лида = мобильный."""
    for p in phones or []:
        v = (p.get("VALUE") or "").strip()
        digits = re.sub(r"\D", "", v)
        if re.match(r"^[78]?9\d{9}$", digits):
            return True
    return False


def parse_comment(text: str) -> tuple[str | None, str | None]:
    """Возвращает (industry, region) из COMMENTS лида."""
    if not text:
        return None, None
    ind = COMMENTS_INDUSTRY_RE.search(text)
    reg = COMMENTS_REGION_RE.search(text)
    return (
        ind.group(1).strip() if ind else None,
        reg.group(1).strip() if reg else None,
    )


def extract_domain(title: str, web: list | None) -> str | None:
    """Сайт из WEB[0] или из конца TITLE."""
    if web:
        for w in web:
            v = (w.get("VALUE") or "").strip()
            if v:
                v = v.replace("https://", "").replace("http://", "").strip("/")
                return v.lower()
    if title:
        m = TITLE_DOMAIN_RE.search(title)
        if m:
            return m.group(1).lower()
    return None


# --- Загрузка xlsx ---

def load_xlsx(path: str = "b24.xlsx") -> dict[str, dict]:
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
        }
    return out


# --- Bitrix ---

async def discover_fields(bx) -> tuple[dict[str, dict], list[dict]]:
    """Ищем поля по совпадению title с FIELD_TARGETS. Возвращает (found, all_uf).
    Город fallback'нет на стандартное ADDRESS_CITY если своего UF_* нет."""
    r = await bx._request("crm.lead.fields", {})
    fields = r.get("result") or {}
    found: dict[str, dict] = {}
    all_uf: list[dict] = []
    for code, meta in fields.items():
        if code.startswith("UF_"):
            all_uf.append({
                "code": code,
                "title": meta.get("title") or meta.get("formLabel"),
                "type": meta.get("type"),
                "items": meta.get("items") or [],
            })
        title = (meta.get("title") or meta.get("formLabel") or "").strip().lower()
        for fkey, candidates in FIELD_TARGETS.items():
            if fkey in found:
                continue
            if not title:
                continue
            if any(c in title for c in candidates):
                found[fkey] = {
                    "code": code,
                    "title": meta.get("title") or meta.get("formLabel"),
                    "type": meta.get("type"),
                    "items": meta.get("items") or [],
                }
    # Если своего UF под «Город» нет — используем стандартное ADDRESS_CITY
    if "city" not in found and "ADDRESS_CITY" in fields:
        meta = fields["ADDRESS_CITY"]
        found["city"] = {
            "code": "ADDRESS_CITY",
            "title": meta.get("title"),
            "type": meta.get("type"),
            "items": [],
        }
    # FIELD_OVERRIDES перекрывают auto-match (когда у поля ещё нет
    # человеческого title в Bitrix)
    for fkey, code in FIELD_OVERRIDES.items():
        if code not in fields:
            logger.warning("Override %s=%s — поля нет в crm.lead.fields", fkey, code)
            continue
        meta = fields[code]
        found[fkey] = {
            "code": code,
            "title": meta.get("title"),
            "type": meta.get("type"),
            "items": meta.get("items") or [],
        }
    return found, all_uf


async def list_b24_leads(bx, extra_select: list[str] | None = None) -> list[dict]:
    """Все лиды с SOURCE_DESCRIPTION='Recon из b24.xlsx'."""
    out: list[dict] = []
    start = 0
    select = ["ID", "TITLE", "COMMENTS", "WEB", "PHONE"] + (extra_select or [])
    while True:
        r = await bx._request("crm.lead.list", {
            "filter": {"%SOURCE_DESCRIPTION": "Recon из b24.xlsx"},
            "select": select,
            "start": start,
        })
        batch = r.get("result", []) or []
        out.extend(batch)
        nxt = r.get("next")
        if nxt is None or not batch:
            break
        start = int(nxt)
    return out


def find_item_id(field_meta: dict, label: str) -> str | None:
    for item in field_meta.get("items") or []:
        if (item.get("VALUE") or "").strip().lower() == label.strip().lower():
            return str(item.get("ID"))
    return None


# --- Основная логика ---

def build_update(
    lead: dict, fin: dict | None, fields_map: dict,
) -> dict:
    """Возвращает словарь поле→значение для crm.lead.update."""
    upd: dict = {}

    industry, region = parse_comment(lead.get("COMMENTS") or "")
    city = extract_city(region)
    phones = lead.get("PHONE") or []
    mobile = has_mobile(phones)
    agency = (fin or {}).get("agency")
    budget_label = bucket_budget((fin or {}).get("revenue"))

    def set_field(fkey: str, value):
        if value is None or value == "":
            return
        meta = fields_map.get(fkey)
        if not meta:
            return
        code = meta["code"]
        # Если поле — enum/список, мапим label на ID
        if meta.get("items"):
            iid = find_item_id(meta, str(value))
            if iid is None:
                # Не нашли подходящего значения в селекте — пропускаем (не пишем
                # «отсебятину» в enum)
                return
            upd[code] = iid
            return
        upd[code] = value

    set_field("agency", agency)
    set_field("city", city)
    set_field("industry", industry)
    # «Есть сотовый» — список Да/Нет, или Boolean Y/N. Поддержим оба.
    if "has_mobile" in fields_map:
        meta = fields_map["has_mobile"]
        if meta.get("items"):
            iid = find_item_id(meta, "Да" if mobile else "Нет")
            if iid:
                upd[meta["code"]] = iid
        elif (meta.get("type") or "").lower() in ("boolean", "bool"):
            upd[meta["code"]] = "Y" if mobile else "N"
        else:
            upd[meta["code"]] = "Да" if mobile else "Нет"
    set_field("budget", budget_label)

    return upd


async def main() -> None:
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--discover", action="store_true",
                   help="только показать что нашлось из UF-полей")
    g.add_argument("--industries", action="store_true",
                   help="собрать уникальные «сферы» в industries.txt")
    g.add_argument("--dry-run", action="store_true",
                   help="не апдейтить, показать что планируется")
    ap.add_argument("--limit", type=int, default=10,
                    help="кол-во лидов при --dry-run / --industries")
    args = ap.parse_args()

    bx = BitrixClient()
    try:
        # 1. Найти UF-коды
        fields_map, all_uf = await discover_fields(bx)
        print("\n=== AUTO-MATCH (по title) ===")
        for k, m in fields_map.items():
            items_summary = ""
            if m.get("items"):
                items_summary = (
                    f"  items={[(i.get('ID'), i.get('VALUE')) for i in m['items'][:6]]}"
                )
            print(f"  {k:12} → {m['code']:30} type={m.get('type')!s:12} «{m.get('title')}»{items_summary}")
        missing = [k for k in FIELD_TARGETS if k not in fields_map]
        if missing:
            print(f"\n⚠ Не нашлось автоматом: {missing}")
            print("Все доступные UF-поля лида (помоги мне ткнуть в нужные):\n")
            for f in all_uf:
                items_summary = ""
                if f.get("items"):
                    items_summary = f"  items={[(i.get('ID'), i.get('VALUE')) for i in f['items'][:6]]}"
                print(f"  {f['code']:35} type={f.get('type')!s:12} «{f.get('title')}»{items_summary}")
        if args.discover:
            return

        # 2. Тянем все наши лиды
        xlsx = load_xlsx()
        print(f"\nЗагрузил xlsx: {len(xlsx)} доменов")
        extra = [m["code"] for m in fields_map.values()]
        leads = await list_b24_leads(bx, extra_select=extra)
        print(f"Найдено b24-лидов: {len(leads)}\n")

        # --industries: собираем уникальные сферы
        if args.industries:
            uniq: dict[str, int] = {}
            for ld in leads:
                ind, _ = parse_comment(ld.get("COMMENTS") or "")
                if ind:
                    key = ind.strip()
                    uniq[key] = uniq.get(key, 0) + 1
            with open("industries.txt", "w", encoding="utf-8") as f:
                for ind, cnt in sorted(uniq.items(), key=lambda x: -x[1]):
                    f.write(f"{cnt:4}  {ind}\n")
            print(f"✓ industries.txt — {len(uniq)} уникальных сфер "
                  f"(топ-10 ниже)")
            for ind, cnt in sorted(uniq.items(), key=lambda x: -x[1])[:10]:
                print(f"  {cnt:4}  {ind}")
            return

        # 3. Обновляем
        sample = leads[:args.limit] if args.dry_run else leads
        ok = 0
        fail = 0
        empty = 0
        for i, ld in enumerate(sample, 1):
            site = extract_domain(ld.get("TITLE") or "", ld.get("WEB"))
            fin = xlsx.get(site) if site else None
            upd = build_update(ld, fin, fields_map)
            if not upd:
                empty += 1
                continue
            if args.dry_run:
                print(f"  [{i}] {ld.get('ID')} {site} → {upd}")
            else:
                try:
                    await bx._request("crm.lead.update", {
                        "id": ld.get("ID"),
                        "fields": upd,
                    })
                    ok += 1
                except Exception as e:
                    fail += 1
                    logger.error("update %s failed: %s", ld.get("ID"), e)
                if i % 50 == 0:
                    print(f"  ...{i}/{len(sample)}: ok={ok} fail={fail} empty={empty}")

        if args.dry_run:
            print(f"\n[DRY-RUN] обработано {len(sample)} лидов, пустых апдейтов: {empty}")
        else:
            print(f"\n=== ИТОГ === ok={ok} fail={fail} пусто={empty} из {len(sample)}")
    finally:
        await bx.close()


asyncio.run(main())
