import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.types import Message

from app import db
from app.config import settings

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(F.text)
async def buffer_message(message: Message):
    """Catch-all: buffer every text message from groups into SQLite."""
    if message.chat.type not in ("group", "supergroup"):
        return

    sender_name = ""
    if message.from_user:
        parts = [message.from_user.first_name or ""]
        if message.from_user.last_name:
            parts.append(message.from_user.last_name)
        sender_name = " ".join(parts).strip()

    tz = ZoneInfo(settings.timezone)
    sent_at = message.date.astimezone(tz) if message.date else datetime.now(tz)

    await db.buffer_message(
        chat_id=message.chat.id,
        sender_id=message.from_user.id if message.from_user else 0,
        sender_name=sender_name,
        text=message.text,
        sent_at=sent_at,
    )
