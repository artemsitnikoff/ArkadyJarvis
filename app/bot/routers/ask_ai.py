import logging

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvis")
router = Router()


class AskAI(StatesGroup):
    waiting_for_question = State()


@router.message(AskAI.waiting_for_question)
async def handle_askai_fsm(message: Message, state: FSMContext, ai_client):
    question = (message.text or "").strip()
    if not question:
        await message.reply("Напиши вопрос текстом.")
        return
    await state.clear()
    await _ask_and_reply(message, question, ai_client=ai_client)


async def _ask_and_reply(message: Message, question: str, *, ai_client):
    logger.info("*** ASK_AI: question=%r from user=%s", question, message.from_user.id)
    wait_msg = await message.reply("🧠 Думаю...")
    try:
        answer = await ai_client.complete(question)
        html_answer = md_to_telegram_html(answer)
        await wait_msg.edit_text(html_answer, reply_markup=MENU_KB)
    except Exception:
        # Claude CLI errors can include stderr / oauth flow hints — keep
        # them out of the user-facing message.
        logger.error("*** ERROR asking AI", exc_info=True)
        await wait_msg.edit_text(
            "❌ Не получилось обратиться к AI. Попробуй ещё раз.",
            reply_markup=MENU_KB,
        )
