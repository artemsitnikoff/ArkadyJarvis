import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvis")
router = Router()


class CreateLead(StatesGroup):
    waiting_for_info = State()

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


@router.message(CreateLead.waiting_for_info)
async def handle_lead_fsm(message: Message, state: FSMContext, ai_client, bitrix, db_user=None):
    text = (message.text or "").strip()
    if not text:
        await message.reply("Напиши данные лида текстом.")
        return
    await state.clear()
    await _create_lead(message, text, ai_client=ai_client, bitrix=bitrix, db_user=db_user)


async def _create_lead(message: Message, text: str, *, ai_client, bitrix, db_user=None):
    raw = await ai_client.complete(EXTRACT_PROMPT.format(text=text))
    parsed = parse_json_response(raw)

    fields: dict = {"TITLE": parsed.get("TITLE") or text[:100]}

    for key in ("NAME", "LAST_NAME", "COMPANY_TITLE", "COMMENTS"):
        if parsed.get(key):
            fields[key] = parsed[key]

    if parsed.get("PHONE"):
        fields["PHONE"] = [{"VALUE": parsed["PHONE"], "VALUE_TYPE": "WORK"}]
    if parsed.get("EMAIL"):
        fields["EMAIL"] = [{"VALUE": parsed["EMAIL"], "VALUE_TYPE": "WORK"}]

    # Source: mark that lead came from Telegram bot
    fields["SOURCE_ID"] = "OTHER"
    fields["SOURCE_DESCRIPTION"] = "Telegram-бот ArkadyJarvis"

    # Add creator's Telegram contact for traceability
    tg_user = message.from_user
    if tg_user:
        username = tg_user.username or ""
        creator_name = tg_user.full_name or ""
        source_parts = [f"Создал: {creator_name}"]
        if username:
            source_parts.append(f"@{username}")
        existing_comments = fields.get("COMMENTS", "")
        tg_info = " | ".join(source_parts)
        fields["COMMENTS"] = f"{existing_comments}\n\n[Telegram] {tg_info}".strip()

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
