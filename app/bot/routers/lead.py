"""FSM CreateLead — лид в Bitrix24 из текста, голосового или фото визитки.

Три входа в одном состоянии `CreateLead.waiting_for_info`:
- текст → Claude (EXTRACT_PROMPT) → JSON полей;
- voice → OpenRouter/Gemini транскрипция → тот же текстовый путь;
- photo / document-картинка → OpenRouter/Gemini vision по prompts/business_card.md →
  JSON полей напрямую (без Claude). Если на фото не визитка — состояние НЕ
  сбрасывается, можно прислать другое фото или написать текстом.
"""
import base64
import html as html_mod
import io
import logging
import os
import tempfile

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from PIL import Image, ImageOps

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.services.prompts import load_prompt
from app.utils import parse_json_response, strip_emoji

logger = logging.getLogger("arkadyjarvis")
router = Router()

# Длинная сторона фото визитки для OCR. 1024 (как у генерации картинок) мало для
# мелкого текста на карточке; 1600 — читаемо и укладывается в разумный payload.
CARD_MAX_SIDE = 1600
CARD_JPEG_QUALITY = 88

# Bitrix IM VALUE_TYPE — допустимые значения для мультиполя IM у лида.
_IM_TYPES = {"TELEGRAM", "WHATSAPP", "VIBER", "VK", "SKYPE", "OTHER"}


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


# ── Голосовое ─────────────────────────────────────────────────

@router.message(CreateLead.waiting_for_info, F.voice)
async def handle_lead_voice(
    message: Message, state: FSMContext, bot: Bot, ai_client, bitrix, openrouter,
    db_user=None,
):
    logger.info("*** LEAD VOICE: duration=%ss from user=%s", message.voice.duration, message.from_user.id)
    wait = await message.reply("🎤 Расшифровываю голосовое...")

    ogg_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tmp:
            ogg_path = tmp.name
        await bot.download(message.voice, destination=ogg_path)

        result = await openrouter.transcribe_voice(ogg_path)
    finally:
        if ogg_path and os.path.exists(ogg_path):
            try:
                os.unlink(ogg_path)
            except Exception as e:
                logger.warning("Failed to delete temp ogg %s: %s", ogg_path, e)

    if not result.success:
        await wait.edit_text(
            f"❌ Не смог расшифровать голосовое: {result.error}\n\n"
            "Попробуй ещё раз или напиши текстом.",
        )
        return

    await wait.edit_text(
        f"✅ Расшифровка (спикеров: {result.speakers_count}):\n\n"
        f"<code>{html_mod.escape(result.full_text)}</code>\n\n"
        "Создаю лид...",
    )
    await state.clear()
    await _create_lead(
        message, result.full_text, ai_client=ai_client, bitrix=bitrix, db_user=db_user,
    )


# ── Фото визитки ──────────────────────────────────────────────

@router.message(CreateLead.waiting_for_info, F.photo)
async def handle_lead_photo(
    message: Message, state: FSMContext, bot: Bot, bitrix, openrouter, db_user=None,
):
    await _handle_card(message, state, bot, message.photo[-1], bitrix, openrouter, db_user)


@router.message(CreateLead.waiting_for_info, F.document)
async def handle_lead_document(
    message: Message, state: FSMContext, bot: Bot, bitrix, openrouter, db_user=None,
):
    mime = (message.document.mime_type or "").lower()
    if not mime.startswith("image/"):
        await message.reply(
            "Это не картинка. Пришли фото визитки (можно файлом без сжатия), "
            "напиши данные текстом или запиши голосовое.",
        )
        return
    await _handle_card(message, state, bot, message.document, bitrix, openrouter, db_user)


async def _handle_card(message, state, bot, tg_file, bitrix, openrouter, db_user) -> None:
    logger.info("*** LEAD CARD: from user=%s", message.from_user.id)
    wait = await message.reply("🪪 Распознаю визитку...")
    try:
        image_b64 = await _download_card_b64(bot, tg_file)
        raw = await openrouter.describe_image(
            load_prompt("business_card"), image_b64, mime="image/jpeg", timeout=120.0,
        )
        parsed = parse_json_response(raw)
    except Exception:
        logger.error("*** ERROR recognizing business card", exc_info=True)
        await wait.edit_text(
            "❌ Не смог распознать визитку. Попробуй другое фото (ровнее, без бликов) "
            "или напиши данные текстом.",
        )
        return

    if not parsed.get("IS_BUSINESS_CARD"):
        seen = (parsed.get("RAW_TEXT") or "").strip()
        tail = f"\n\nЧто разобрал:\n<code>{html_mod.escape(seen[:800])}</code>" if seen else ""
        await wait.edit_text(
            "🤔 Не похоже на визитку. Пришли другое фото или напиши данные текстом."
            f"{tail}",
        )
        return  # состояние не сбрасываем — ждём следующую попытку

    await state.clear()
    fields = _card_to_fields(parsed, caption=message.caption, tg_user=message.from_user)
    await wait.edit_text("🪪 Визитка распознана, создаю лид...")
    await _finalize_lead(message, fields, bitrix=bitrix)


async def _download_card_b64(bot: Bot, tg_file) -> str:
    """Скачать фото/документ, развернуть по EXIF, ужать до CARD_MAX_SIDE, → base64 JPEG."""
    buf_in = await bot.download(tg_file)
    img = Image.open(buf_in)
    img = ImageOps.exif_transpose(img)  # фото с телефона файлом несёт поворот в EXIF
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    if max(img.size) > CARD_MAX_SIDE:
        img.thumbnail((CARD_MAX_SIDE, CARD_MAX_SIDE))
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=CARD_JPEG_QUALITY)
    return base64.b64encode(out.getvalue()).decode()


def _clean_list(values) -> list[str]:
    """Список строк без пустых и дублей (с сохранением порядка)."""
    seen: set[str] = set()
    result: list[str] = []
    for v in values or []:
        s = str(v or "").strip()
        if s and s.lower() not in seen:
            seen.add(s.lower())
            result.append(s)
    return result


def _card_to_fields(parsed: dict, *, caption: str | None, tg_user) -> dict:
    """JSON из business_card.md → поля crm.lead.add. Чистая функция (тестируется без бота)."""
    def s(key: str) -> str:
        v = parsed.get(key)
        return str(v).strip() if v else ""

    last_name, name, second = s("LAST_NAME"), s("NAME"), s("SECOND_NAME")
    company, post = s("COMPANY_TITLE"), s("POST")
    phones = _clean_list(parsed.get("PHONES"))
    emails = _clean_list(parsed.get("EMAILS"))
    webs = _clean_list(parsed.get("WEB"))

    person = " ".join(filter(None, [last_name, name]))
    title = " — ".join(filter(None, [company, person])) or person or company
    if not title:
        title = f"Визитка {phones[0]}" if phones else "Лид с визитки"

    fields: dict = {"TITLE": title[:200]}
    if name:
        fields["NAME"] = name
    if last_name:
        fields["LAST_NAME"] = last_name
    if second:
        fields["SECOND_NAME"] = second
    if post:
        fields["POST"] = post
    if company:
        fields["COMPANY_TITLE"] = company
    if s("ADDRESS"):
        fields["ADDRESS"] = s("ADDRESS")
    if phones:
        fields["PHONE"] = [{"VALUE": p, "VALUE_TYPE": "WORK"} for p in phones]
    if emails:
        fields["EMAIL"] = [{"VALUE": e.lower(), "VALUE_TYPE": "WORK"} for e in emails]
    if webs:
        fields["WEB"] = [{"VALUE": w, "VALUE_TYPE": "WORK"} for w in webs]

    ims: list[dict] = []
    for im in parsed.get("IM") or []:
        if not isinstance(im, dict):
            continue
        val = str(im.get("VALUE") or "").strip()
        if not val:
            continue
        typ = str(im.get("TYPE") or "OTHER").upper()
        ims.append({"VALUE": val, "VALUE_TYPE": typ if typ in _IM_TYPES else "OTHER"})
    if ims:
        fields["IM"] = ims

    comment_lines = ["Лид с фото визитки (Telegram-бот ArkadyJarvis)."]
    if s("INDUSTRY"):
        comment_lines.append(f"Сфера: {s('INDUSTRY')}")
    if s("EXTRA"):
        comment_lines.append(f"Доп: {s('EXTRA')}")
    if caption and caption.strip():
        comment_lines.append(f"Комментарий отправителя: {caption.strip()}")
    if s("RAW_TEXT"):
        comment_lines.append("")
        comment_lines.append("Текст визитки:")
        comment_lines.append(s("RAW_TEXT"))
    fields["COMMENTS"] = "\n".join(comment_lines)

    fields["SOURCE_ID"] = "OTHER"
    fields["SOURCE_DESCRIPTION"] = "Telegram-бот ArkadyJarvis (визитка)"
    _append_creator(fields, tg_user)
    return fields


# ── Текст ─────────────────────────────────────────────────────

@router.message(CreateLead.waiting_for_info)
async def handle_lead_fsm(message: Message, state: FSMContext, ai_client, bitrix, db_user=None):
    text = (message.text or "").strip()
    if not text:
        await message.reply(
            "Напиши данные лида текстом, запиши голосовое или пришли фото визитки.",
        )
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
    _append_creator(fields, message.from_user)
    await _finalize_lead(message, fields, bitrix=bitrix)


# ── Общее ─────────────────────────────────────────────────────

def _append_creator(fields: dict, tg_user) -> None:
    """Дописывает в COMMENTS, кто создал лид (для трассировки), и чистит emoji —
    Bitrix COMMENTS это MySQL utf8 (3 байта), 4-байтные символы обрезают поле."""
    if tg_user:
        username = tg_user.username or ""
        creator_name = tg_user.full_name or ""
        source_parts = [f"Создал: {creator_name}"]
        if username:
            source_parts.append(f"@{username}")
        existing_comments = fields.get("COMMENTS", "")
        tg_info = " | ".join(source_parts)
        fields["COMMENTS"] = f"{existing_comments}\n\n[Telegram] {tg_info}".strip()
    if fields.get("COMMENTS"):
        fields["COMMENTS"] = strip_emoji(fields["COMMENTS"])


async def _finalize_lead(message: Message, fields: dict, *, bitrix) -> None:
    result = await bitrix.create_lead(fields)

    lead_id = result.get("id", "?")
    bitrix_url = f"https://{settings.bitrix_domain}/crm/lead/details/{lead_id}/"

    esc = html_mod.escape
    reply_parts = [f"✅ Лид создан (id: {esc(str(lead_id))})"]
    reply_parts.append(f"📋 {esc(fields['TITLE'])}")
    reply_parts.append(f"🔗 {bitrix_url}")
    name = " ".join(filter(None, [
        fields.get("LAST_NAME"), fields.get("NAME"), fields.get("SECOND_NAME"),
    ]))
    if name:
        reply_parts.append(f"👤 {esc(name)}")
    if fields.get("POST"):
        reply_parts.append(f"💼 {esc(fields['POST'])}")
    if fields.get("COMPANY_TITLE"):
        reply_parts.append(f"🏢 {esc(fields['COMPANY_TITLE'])}")
    for p in fields.get("PHONE") or []:
        reply_parts.append(f"📞 {esc(p['VALUE'])}")
    for e in fields.get("EMAIL") or []:
        reply_parts.append(f"📧 {esc(e['VALUE'])}")
    for w in fields.get("WEB") or []:
        reply_parts.append(f"🌐 {esc(w['VALUE'])}")
    for im in fields.get("IM") or []:
        reply_parts.append(f"💬 {esc(im['VALUE_TYPE'].title())}: {esc(im['VALUE'])}")

    await message.reply("\n".join(reply_parts), reply_markup=MENU_KB)
    logger.info("*** Lead created: id=%s fields=%s", lead_id, fields)
