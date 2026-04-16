import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(Command("summary"))
async def handle_summarize(message: Message, ai_client):
    chat_id = message.chat.id
    logger.info("*** /summary in chat=%s from user=%s", chat_id, message.from_user.id)

    from app.summarizer import summarize_from_buffer
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from app.config import settings

    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    summary = await summarize_from_buffer(chat_id, ai_client=ai_client, since=start_of_day)
    await message.reply(f"📊 #summary\n\n{summary}", reply_markup=MENU_KB)
    logger.info("*** SENT summary reply to chat=%s", chat_id)
