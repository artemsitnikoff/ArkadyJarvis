"""Штирлиц — оркестратор разведки по контрагенту или человеку.

- Компания (ИНН/название) → DaData + ГИР БО + WebSearch (новости/тендеры)
- Человек (ФИО + контекст)  → WebSearch (LinkedIn, Habr, VK, новости)
"""
import json
import logging
import re

from app.services.ai_client import AIClient
from app.services.dadata_client import DaDataClient
from app.services.giro_client import GiroClient
from app.services.prompts import load_prompt

logger = logging.getLogger("arkadyjarvis")

COMPANY_PROMPT = load_prompt("stirlitz")
PERSON_PROMPT = load_prompt("stirlitz_person")

INN_PATTERN = re.compile(r"^\d{10}(\d{2})?$")
ORG_FORM_PATTERN = re.compile(
    r"\b(ООО|ПАО|АО|ОАО|ЗАО|ИП|НКО|АНО|ФОНД|МУП|ГУП|БАНК|ФГУП|ФГУ|УЧРЕЖДЕНИЕ)\b",
    re.IGNORECASE,
)


def _classify(query: str) -> str:
    """Returns 'company' | 'person' | 'unknown' via simple heuristics."""
    q = query.strip()
    if INN_PATTERN.match(q):
        return "company"
    if ORG_FORM_PATTERN.search(q):
        return "company"
    # Множественное упоминание заглавных слов — человек или компания. Если 2-4 слова,
    # все начинаются с большой и заканчиваются строчными → ФИО.
    parts = [p for p in q.split() if p]
    if 2 <= len(parts) <= 4 and all(
        p[0].isupper() and (len(p) == 1 or p[1:].islower()) for p in parts if p[0].isalpha()
    ):
        return "person"
    return "unknown"


async def _classify_with_ai(query: str, ai_client: AIClient) -> str:
    """Fallback classifier — small Claude call."""
    prompt = (
        f"Запрос: «{query}»\n\n"
        "Что это: организация (юр.лицо, ИП, бренд) или физическое лицо (человек)?\n"
        "Ответь ОДНИМ словом без пояснений: «company» или «person» или «unknown»."
    )
    try:
        answer = await ai_client.complete(prompt, timeout=30)
        word = answer.strip().lower().split()[0] if answer.strip() else "unknown"
        word = word.strip(".,!?\"'«»()[]")
        if "company" in word or "org" in word or "юр" in word:
            return "company"
        if "person" in word or "human" in word or "челов" in word:
            return "person"
        return "unknown"
    except Exception as e:
        logger.warning("Stirlitz classify AI fallback failed: %s", e)
        return "unknown"


async def resolve_inn(query: str, dadata: DaDataClient) -> tuple[str | None, list[dict]]:
    q = query.strip()
    if INN_PATTERN.match(q):
        item = await dadata.find_by_id(q)
        return q, ([item] if item else [])
    sug = await dadata.suggest(q, count=5)
    if not sug:
        return None, []
    first_inn = (sug[0].get("data") or {}).get("inn")
    return first_inn, sug


async def gather_company_intel(
    inn: str, dadata: DaDataClient, giro: GiroClient,
) -> dict:
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
        "giro": giro_summary,
    }


async def _build_company_card(
    query: str,
    ai_client: AIClient,
    dadata: DaDataClient,
    giro: GiroClient,
) -> tuple[str | None, list[dict], str | None]:
    if not dadata.is_configured:
        return None, [], "DaData не настроена в конфиге"
    inn, suggestions = await resolve_inn(query, dadata)
    if not inn:
        return None, [], f"Не нашёл компанию по запросу «{query}»"
    if not suggestions:
        return None, [], f"Не нашёл компанию по ИНН {inn}"

    data = await gather_company_intel(inn, dadata, giro)
    data_json = json.dumps(data, ensure_ascii=False, indent=2, default=str)
    prompt = COMPANY_PROMPT.replace("{data_json}", data_json)
    try:
        card = await ai_client.complete(
            prompt, timeout=180, allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.error("Stirlitz company card failed: %s", e, exc_info=True)
        return None, suggestions, f"AI-анализ упал: {e}"
    return card, suggestions, None


async def _build_person_card(
    query: str, ai_client: AIClient,
) -> tuple[str | None, list[dict], str | None]:
    prompt = PERSON_PROMPT.replace("{query}", query)
    try:
        card = await ai_client.complete(
            prompt, timeout=180, allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.error("Stirlitz person card failed: %s", e, exc_info=True)
        return None, [], f"AI-анализ упал: {e}"
    return card, [], None


async def build_intel_card(
    query: str,
    ai_client: AIClient,
    dadata: DaDataClient,
    giro: GiroClient,
) -> tuple[str | None, list[dict], str | None, str]:
    """Главная точка входа. Возвращает (card_html, suggestions, error_text, kind).
    kind ∈ {'company', 'person'} — что в итоге решили обследовать."""
    kind = _classify(query)
    if kind == "unknown":
        kind = await _classify_with_ai(query, ai_client)
    if kind == "unknown":
        # По-умолчанию пробуем компанию (DaData умеет искать по любой строке)
        kind = "company"

    if kind == "person":
        card, sug, err = await _build_person_card(query, ai_client)
    else:
        card, sug, err = await _build_company_card(query, ai_client, dadata, giro)
    return card, sug, err, kind
