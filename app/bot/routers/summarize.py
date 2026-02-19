import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(Command("summary"))
@router.message(F.text.lower() == "суммаризация")
async def handle_summarize(message: Message):
    chat_id = message.chat.id
    logger.info("*** TRIGGER: суммаризация in chat=%s from user=%s", chat_id, message.from_user.id)
    try:
        from app.summarizer import summarize_from_buffer
        from datetime import datetime
        from zoneinfo import ZoneInfo
        from app.config import settings

        tz = ZoneInfo(settings.timezone)
        start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
        summary = await summarize_from_buffer(chat_id, since=start_of_day)
        await message.reply(f"📊 #summary\n\n{summary}", reply_markup=MENU_KB)
        logger.info("*** SENT summary reply to chat=%s", chat_id)
    except Exception as e:
        logger.error("*** ERROR summarizing: %s", e, exc_info=True)
        await message.reply(f"❌ Ошибка суммаризации: {e}")
