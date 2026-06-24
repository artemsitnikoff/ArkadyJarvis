import html
import io
import logging

from aiogram import Bot, F, Router
from aiogram.exceptions import TelegramBadRequest
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from app.bot.routers.start import MENU_KB
from app.services.document_parser import UnsupportedDocumentError, extract_text
from app.services.prompts import load_prompt
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvis")
router = Router()

MAX_DOC_CHARS = 120_000
TELEGRAM_MSG_LIMIT = 4000


class ContractCheck(StatesGroup):
    waiting_for_document = State()


@router.message(ContractCheck.waiting_for_document, F.document)
async def handle_contract_document(message: Message, state: FSMContext, bot: Bot, ai_client):
    doc = message.document
    filename = doc.file_name or "document"
    logger.info(
        "*** CONTRACT: file=%s size=%s from user=%s",
        filename, doc.file_size, message.from_user.id,
    )

    await state.clear()
    wait_msg = await message.reply("📄 Читаю договор...")

    buffer = io.BytesIO()
    await bot.download(doc, destination=buffer)
    file_bytes = buffer.getvalue()

    try:
        text = extract_text(file_bytes, filename)
    except UnsupportedDocumentError as e:
        # Curated message from our own exception class — safe to show.
        await wait_msg.edit_text(f"❌ {e}", reply_markup=MENU_KB)
        return
    except Exception:
        logger.error("*** ERROR parsing contract", exc_info=True)
        await wait_msg.edit_text(
            "❌ Не удалось прочитать файл.", reply_markup=MENU_KB,
        )
        return

    if not text.strip():
        await wait_msg.edit_text(
            "❌ В файле не нашёл текста. Возможно, это скан без OCR.",
            reply_markup=MENU_KB,
        )
        return

    if len(text) > MAX_DOC_CHARS:
        text = text[:MAX_DOC_CHARS]
        logger.warning("Contract truncated to %d chars", MAX_DOC_CHARS)

    await wait_msg.edit_text("🔍 Проверяю по правилам...")

    prompt_template = load_prompt("contract_check")
    full_prompt = f"{prompt_template}\n\n---\n\nТекст документа для проверки:\n\n{text}"

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception:
        logger.error("*** ERROR checking contract", exc_info=True)
        await wait_msg.edit_text(
            "❌ Ошибка проверки. Попробуй ещё раз.", reply_markup=MENU_KB,
        )
        return

    html_answer = md_to_telegram_html(answer)
    header = f"📄 <b>Проверка договора:</b> {html.escape(filename)}\n\n"
    body = header + html_answer

    async def _send_as_md() -> None:
        # Фолбэк: отдаём сырой ответ .md-файлом. Нужен и для длинных ответов,
        # и когда Telegram отверг HTML — md_to_telegram_html на «грязном»
        # markdown от AI может выдать перекрывающиеся <b>/<i> теги
        # ("can't parse entities"). Главное — не потерять результат проверки.
        try:
            await wait_msg.delete()
        except Exception:
            pass
        preview = answer[:300].rstrip() + ("..." if len(answer) > 300 else "")
        doc_file = BufferedInputFile(answer.encode("utf-8"), filename="contract_check.md")
        await message.answer_document(
            doc_file,
            caption=f"📄 Проверка договора: {filename}\n\n{preview}",
            parse_mode=None,
            reply_markup=MENU_KB,
        )

    if len(body) <= TELEGRAM_MSG_LIMIT:
        try:
            await wait_msg.edit_text(body, reply_markup=MENU_KB)
        except TelegramBadRequest:
            logger.warning("Contract: Telegram отверг HTML, шлю .md", exc_info=True)
            await _send_as_md()
        return

    # Длинный ответ — сразу файлом, не чанкуем HTML.
    await _send_as_md()


@router.message(ContractCheck.waiting_for_document)
async def handle_contract_not_a_document(message: Message):
    await message.reply("📄 Пришли файл договора (PDF, DOCX или TXT) как документ.")
