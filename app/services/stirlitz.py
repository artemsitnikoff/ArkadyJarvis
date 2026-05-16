"""Штирлиц — оркестратор разведки по контрагенту.

Принимает ИНН или название → DaData + ГИР БО → JSON → Claude → карточка.
"""
import json
import logging
import re

from app.services.ai_client import AIClient
from app.services.dadata_client import DaDataClient
from app.services.giro_client import GiroClient
from app.services.prompts import load_prompt

logger = logging.getLogger("arkadyjarvis")

PROMPT_TEMPLATE = load_prompt("stirlitz")
INN_PATTERN = re.compile(r"^\d{10}(\d{2})?$")  # ЮЛ=10, ИП=12


async def resolve_inn(query: str, dadata: DaDataClient) -> tuple[str | None, list[dict]]:
    """Возвращает (inn, suggestions).
    Если query — ИНН, suggestions содержит 1 элемент.
    Если query — название, suggestions — список вариантов."""
    q = query.strip()
    if INN_PATTERN.match(q):
        item = await dadata.find_by_id(q)
        return q, ([item] if item else [])
    sug = await dadata.suggest(q, count=5)
    if not sug:
        return None, []
    first_inn = (sug[0].get("data") or {}).get("inn")
    return first_inn, sug


async def gather_intelligence(
    inn: str, dadata: DaDataClient, giro: GiroClient,
) -> dict:
    """Собирает сырые данные обо всех источниках."""
    dadata_item = await dadata.find_by_id(inn)
    giro_summary = await giro.get_summary(inn)

    dd = (dadata_item or {}).get("data") or {}
    return {
        "name": (dadata_item or {}).get("value"),
        "inn": inn,
        "ogrn": dd.get("ogrn"),
        "kpp": dd.get("kpp"),
        "opf": (dd.get("opf") or {}).get("full"),
        "opf_short": (dd.get("opf") or {}).get("short"),
        "status": (dd.get("state") or {}).get("status"),
        "registration_date_ms": (dd.get("state") or {}).get("registration_date"),
        "liquidation_date_ms": (dd.get("state") or {}).get("liquidation_date"),
        "address": (dd.get("address") or {}).get("unrestricted_value"),
        "region": (((dd.get("address") or {}).get("data") or {})).get("region_with_type"),
        "city": (((dd.get("address") or {}).get("data") or {})).get("city_with_type"),
        "okved": dd.get("okved"),
        "management": dd.get("management"),
        "founders": dd.get("founders"),
        "branch_type": dd.get("branch_type"),
        "branch_count": dd.get("branch_count"),
        "type": dd.get("type"),
        # ГИР БО — финансы по годам
        "giro": giro_summary,
    }


async def build_intel_card(
    query: str,
    ai_client: AIClient,
    dadata: DaDataClient,
    giro: GiroClient,
) -> tuple[str | None, list[dict], str | None]:
    """Главная точка входа. Возвращает (card_html, suggestions, error_text)."""
    if not dadata.is_configured:
        return None, [], "DaData не настроена в конфиге"

    inn, suggestions = await resolve_inn(query, dadata)
    if not inn:
        return None, [], f"Не нашёл компанию по запросу {query!r}"
    if not suggestions:
        return None, [], f"Не нашёл компанию по ИНН {inn}"

    data = await gather_intelligence(inn, dadata, giro)
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    prompt = PROMPT_TEMPLATE.replace("{data_json}", data_json)
    try:
        card = await ai_client.complete(prompt, timeout=120)
    except Exception as e:
        logger.error("Stirlitz Claude call failed: %s", e, exc_info=True)
        return None, suggestions, f"AI-анализ упал: {e}"
    return card, suggestions, None
