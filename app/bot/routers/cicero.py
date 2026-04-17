import io
import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.document_parser import UnsupportedDocumentError, extract_text
from app.services.prompts import load_prompt
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvis")
router = Router()

MAX_DOC_CHARS = 120_000
TELEGRAM_MSG_LIMIT = 4000

EXIT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")],
])


class Cicero(StatesGroup):
    chatting = State()


@router.message(Cicero.chatting, F.document)
async def handle_cicero_document(message: Message, state: FSMContext, bot: Bot, ai_client):
    doc = message.document
    filename = doc.file_name or "document"
    question = (message.caption or "").strip()

    logger.info(
        "*** CICERO DOC: file=%s question=%r from user=%s",
        filename, question, message.from_user.id,
    )

    wait_msg = await message.reply("⚖️ Читаю документ...")
    buffer = io.BytesIO()
    await bot.download(doc, destination=buffer)

    try:
        doc_text = extract_text(buffer.getvalue(), filename)
    except UnsupportedDocumentError as e:
        await wait_msg.edit_text(f"❌ {e}", reply_markup=EXIT_KB)
        return
    except Exception as e:
        logger.error("*** ERROR parsing cicero doc: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Не удалось прочитать файл: {e}", reply_markup=EXIT_KB)
        return

    if not doc_text.strip():
        await wait_msg.edit_text(
            "❌ В файле не нашёл текста. Возможно, это скан без OCR.",
            reply_markup=EXIT_KB,
        )
        return

    if len(doc_text) > MAX_DOC_CHARS:
        doc_text = doc_text[:MAX_DOC_CHARS]
        logger.warning("Cicero doc truncated to %d chars", MAX_DOC_CHARS)

    await wait_msg.edit_text("⚖️ Анализирую...")

    system = load_prompt("cicero")
    user_part = question or "Проанализируй приложенный документ: основные условия, риски, рекомендации."
    full_prompt = (
        f"{system}\n\n---\n\n"
        f"Текст приложенного документа ({filename}):\n\n{doc_text}\n\n---\n\n"
        f"Вопрос пользователя: {user_part}"
    )

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception as e:
        logger.error("*** ERROR cicero doc: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Ошибка: {e}", reply_markup=EXIT_KB)
        return

    await _send_answer(message, wait_msg, answer)


@router.message(Cicero.chatting, F.text)
async def handle_cicero_text(message: Message, state: FSMContext, ai_client):
    question = (message.text or "").strip()
    if not question:
        await message.reply("Задай вопрос текстом или приложи документ с вопросом в подписи.")
        return

    logger.info("*** CICERO TEXT: q=%r from user=%s", question, message.from_user.id)
    wait_msg = await message.reply("⚖️ Думаю...")

    system = load_prompt("cicero")
    full_prompt = f"{system}\n\n---\n\nВопрос пользователя: {question}"

    try:
        answer = await ai_client.complete(full_prompt, timeout=300)
    except Exception as e:
        logger.error("*** ERROR cicero text: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Ошибка: {e}", reply_markup=EXIT_KB)
        return

    await _send_answer(message, wait_msg, answer)


async def _send_answer(message: Message, wait_msg: Message, answer: str):
    html_answer = md_to_telegram_html(answer)
    if len(html_answer) <= TELEGRAM_MSG_LIMIT:
        await wait_msg.edit_text(html_answer, reply_markup=EXIT_KB)
        return

    await wait_msg.delete()
    for chunk_start in range(0, len(html_answer), TELEGRAM_MSG_LIMIT):
        chunk = html_answer[chunk_start:chunk_start + TELEGRAM_MSG_LIMIT]
        is_last = chunk_start + TELEGRAM_MSG_LIMIT >= len(html_answer)
        await message.answer(chunk, reply_markup=EXIT_KB if is_last else None)
