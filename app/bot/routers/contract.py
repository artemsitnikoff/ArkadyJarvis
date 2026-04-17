import io
import logging

from aiogram import Bot, F, Router
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
        await wait_msg.edit_text(f"❌ {e}", reply_markup=MENU_KB)
        return
    except Exception as e:
        logger.error("*** ERROR parsing contract: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Не удалось прочитать файл: {e}", reply_markup=MENU_KB)
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
    except Exception as e:
        logger.error("*** ERROR checking contract: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Ошибка проверки: {e}", reply_markup=MENU_KB)
        return

    html_answer = md_to_telegram_html(answer)
    header = f"📄 <b>Проверка договора:</b> {filename}\n\n"
    body = header + html_answer
    if len(body) <= TELEGRAM_MSG_LIMIT:
        await wait_msg.edit_text(body, reply_markup=MENU_KB)
        return

    # Long answer — send as a .md file to avoid breaking HTML entities across chunks
    await wait_msg.delete()
    preview = answer[:300].rstrip() + ("..." if len(answer) > 300 else "")
    file = BufferedInputFile(answer.encode("utf-8"), filename="contract_check.md")
    await message.answer_document(
        file,
        caption=f"{header.strip()}\n\n{preview}",
        reply_markup=MENU_KB,
    )


@router.message(ContractCheck.waiting_for_document)
async def handle_contract_not_a_document(message: Message):
    await message.reply("📄 Пришли файл договора (PDF, DOCX или TXT) как документ.")
