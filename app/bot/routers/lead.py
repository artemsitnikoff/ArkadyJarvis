import json
import logging
import re

from aiogram import F, Router
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.services.ai_client import AIClient
from app.services.bitrix_client import BitrixClient

logger = logging.getLogger("arkadyjarvis")
router = Router()

EXTRACT_PROMPT = """\
Из текста ниже извлеки данные для создания CRM-лида. Верни JSON (только JSON, без markdown).
Поля:
- TITLE (строка, обязательно) — краткое название лида
- NAME (строка|null) — имя контакта
- LAST_NAME (строка|null) — фамилия контакта
- COMPANY_TITLE (строка|null) — название компании
- PHONE (строка|null) — телефон (любой формат)
- EMAIL (строка|null) — email
- COMMENTS (строка|null) — дополнительная информация

Если поле не найдено — null. TITLE обязателен, если нет явного названия — сформулируй из контекста.

Текст:
{text}
"""


@router.message(F.text.regexp(r"(?i)^(сделай|создай)\s+лид"))
async def handle_create_lead(message: Message):
    text = message.text or ""
    logger.info("*** TRIGGER: 'создай лид' in chat=%s from user=%s", message.chat.id, message.from_user.id)

    try:
        body = re.sub(r"(?i)^(сделай|создай)\s+лид\s*", "", text).strip()

        reply_text = ""
        if message.reply_to_message and message.reply_to_message.text:
            reply_text = message.reply_to_message.text.strip()

        combined = "\n".join(filter(None, [body, reply_text]))
        if not combined:
            await message.reply("Напиши данные лида или реплайни на сообщение с информацией.")
            return

        ai = AIClient.get()
        raw = await ai.complete(EXTRACT_PROMPT.format(text=combined), max_tokens=512, temperature=0.2)

        raw = re.sub(r"^```(?:json)?\s*", "", raw.strip())
        raw = re.sub(r"\s*```$", "", raw)

        parsed = json.loads(raw)

        fields: dict = {"TITLE": parsed.get("TITLE") or combined[:100]}

        if parsed.get("NAME"):
            fields["NAME"] = parsed["NAME"]
        if parsed.get("LAST_NAME"):
            fields["LAST_NAME"] = parsed["LAST_NAME"]
        if parsed.get("COMPANY_TITLE"):
            fields["COMPANY_TITLE"] = parsed["COMPANY_TITLE"]
        if parsed.get("PHONE"):
            fields["PHONE"] = [{"VALUE": parsed["PHONE"], "VALUE_TYPE": "WORK"}]
        if parsed.get("EMAIL"):
            fields["EMAIL"] = [{"VALUE": parsed["EMAIL"], "VALUE_TYPE": "WORK"}]
        if parsed.get("COMMENTS"):
            fields["COMMENTS"] = parsed["COMMENTS"]

        bitrix = BitrixClient.get()
        result = await bitrix.create_lead(fields)

        lead_id = result.get("id", "?")
        bitrix_url = f"https://{settings.bitrix_domain}/crm/lead/details/{lead_id}/"

        reply_parts = [f"✅ Лид создан (id: {lead_id})"]
        reply_parts.append(f"📋 {fields['TITLE']}")
        reply_parts.append(f"🔗 {bitrix_url}")
        name = " ".join(filter(None, [fields.get("NAME"), fields.get("LAST_NAME")]))
        if name:
            reply_parts.append(f"👤 {name}")
        if fields.get("COMPANY_TITLE"):
            reply_parts.append(f"🏢 {fields['COMPANY_TITLE']}")
        if parsed.get("PHONE"):
            reply_parts.append(f"📞 {parsed['PHONE']}")
        if parsed.get("EMAIL"):
            reply_parts.append(f"📧 {parsed['EMAIL']}")

        await message.reply("\n".join(reply_parts), reply_markup=MENU_KB)
        logger.info("*** Lead created: id=%s fields=%s", lead_id, fields)

    except json.JSONDecodeError:
        await message.reply("❌ Не удалось разобрать данные лида. Попробуй ещё раз.")
    except Exception as e:
        logger.error("*** ERROR creating lead: %s", e, exc_info=True)
        await message.reply(f"❌ Ошибка создания лида: {e}")
