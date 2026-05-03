import logging
from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import Message

from app.config import settings
from app.services.zabbix_monitor import process_alert_message

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.channel_post(F.chat.id == settings.zabbix_channel_id, F.text)
async def handle_zabbix_post(message: Message):
    """Real-time ingest of Zabbix alerts from the configured channel."""
    text = message.text or ""
    ts = message.date or datetime.now(timezone.utc)
    try:
        await process_alert_message(text, ts)
    except Exception as e:
        logger.error("Zabbix channel_post handler error: %s", e, exc_info=True)
