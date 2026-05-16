"""Штирлиц — оркестратор разведки.

Не использует регулярки для классификации — Claude (Haiku) сам понимает
из истории сообщений что искать (ИНН / название / ФИО) и при недостатке
данных задаёт уточняющий вопрос.
"""
import logging

from app.services.ai_client import AIClient
from app.services.dadata_client import DaDataClient
from app.services.giro_client import GiroClient
from app.services.prompts import load_prompt
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvis")

COMPANY_PROMPT = load_prompt("stirlitz")
PERSON_PROMPT = load_prompt("stirlitz_person")
INTENT_PROMPT = load_prompt("stirlitz_intent")

# Дешёвая быстрая модель для классификации намерения
CLASSIFIER_MODEL = "haiku"


async def classify_intent(history: list[str], ai_client: AIClient) -> dict:
    """Спрашивает Haiku что хочет пользователь. Возвращает dict с полем `kind`:
    company_inn / company_name / person / clarify (+ конкретные поля)."""
    history_text = "\n".join(f"- {h}" for h in history if h.strip())
    prompt = INTENT_PROMPT.replace("{history}", history_text or "(пусто)")
    try:
        raw = await ai_client.complete(prompt, timeout=30, model=CLASSIFIER_MODEL)
    except Exception as e:
        logger.warning("Stirlitz classifier failed: %s", e)
        return {"kind": "clarify", "question": "Не удалось понять запрос — повтори формулировку."}
    try:
        return parse_json_response(raw)
    except Exception as e:
        logger.warning("Stirlitz classifier JSON parse failed: %s | raw=%r", e, raw[:200])
        return {"kind": "clarify", "question": "Не понял запрос — введи ИНН компании или ФИО человека."}


# ── COMPANY FLOW ──────────────────────────────────────────────────────

async def _resolve_company(
    intent: dict, dadata: DaDataClient,
) -> tuple[str | None, list[dict]]:
    """По dict-намерению ищет ИНН + suggestions."""
    if intent.get("kind") == "company_inn":
        inn = intent.get("inn") or ""
        item = await dadata.find_by_id(inn)
        return inn, ([item] if item else [])
    # company_name
    name = intent.get("name") or ""
    sug = await dadata.suggest(name, count=5)
    if not sug:
        return None, []
    first_inn = (sug[0].get("data") or {}).get("inn")
    return first_inn, sug


async def gather_company_intel(inn: str, dadata: DaDataClient, giro: GiroClient) -> dict:
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


async def build_company_card(
    intent: dict, ai_client: AIClient, dadata: DaDataClient, giro: GiroClient,
) -> tuple[str | None, list[dict], str | None]:
    if not dadata.is_configured:
        return None, [], "DaData не настроена в конфиге"
    inn, suggestions = await _resolve_company(intent, dadata)
    if not inn:
        return None, [], "Не нашёл такую компанию в ЕГРЮЛ"
    if not suggestions:
        return None, [], f"ИНН {inn} не найден в ЕГРЮЛ"

    import json
    data = await gather_company_intel(inn, dadata, giro)
    prompt = COMPANY_PROMPT.replace(
        "{data_json}", json.dumps(data, ensure_ascii=False, indent=2, default=str),
    )
    try:
        card = await ai_client.complete(
            prompt, timeout=180, allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.error("Stirlitz company card failed: %s", e, exc_info=True)
        return None, suggestions, f"AI-анализ упал: {e}"
    return card, suggestions, None


# ── PERSON FLOW ───────────────────────────────────────────────────────

async def build_person_card(
    intent: dict, ai_client: AIClient,
) -> tuple[str | None, list[dict], str | None]:
    full_name = intent.get("full_name") or ""
    context = intent.get("context") or ""
    query = f"{full_name} {context}".strip()
    prompt = PERSON_PROMPT.replace("{query}", query)
    try:
        card = await ai_client.complete(
            prompt, timeout=180, allowed_tools="WebSearch,WebFetch",
        )
    except Exception as e:
        logger.error("Stirlitz person card failed: %s", e, exc_info=True)
        return None, [], f"AI-анализ упал: {e}"
    return card, [], None
