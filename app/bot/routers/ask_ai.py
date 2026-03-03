import logging
import re

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvis")
router = Router()


class AskAI(StatesGroup):
    waiting_for_question = State()


@router.message(F.text.regexp(r"(?i)^(спроси\s+ai|вопрос)\s"))
async def handle_askai_text(message: Message, openrouter):
    text = message.text or ""
    question = re.sub(r"(?i)^(спроси\s+ai|вопрос)\s+", "", text).strip()
    if not question:
        await message.reply("Напиши вопрос после команды.")
        return
    await _ask_and_reply(message, question, openrouter=openrouter)


@router.message(AskAI.waiting_for_question)
async def handle_askai_fsm(message: Message, state: FSMContext, openrouter):
    question = (message.text or "").strip()
    if not question:
        await message.reply("Напиши вопрос текстом.")
        return
    await state.clear()
    await _ask_and_reply(message, question, openrouter=openrouter)


async def _ask_and_reply(message: Message, question: str, *, openrouter):
    logger.info("*** ASK_AI: question=%r from user=%s", question, message.from_user.id)
    wait_msg = await message.reply("🧠 Думаю...")
    try:
        answer = await openrouter.ask_opus(question)
        html_answer = md_to_telegram_html(answer)
        await wait_msg.edit_text(html_answer, reply_markup=MENU_KB)
    except Exception as e:
        logger.error("*** ERROR asking AI: %s", e, exc_info=True)
        await wait_msg.edit_text(f"❌ Ошибка: {e}")
