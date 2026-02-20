import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from app import db
from app.config import settings
from app.services.ai_client import AIClient
from app.summarizer import build_daily_overview, summarize_messages

logger = logging.getLogger("arkadyjarvis")


async def daily_summary_job(bot: Bot, ai_client: AIClient):
    """Summarize each group chat and send the result to the group."""
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    groups = await db.get_all_group_chats()
    if not groups:
        logger.info("=== No group chats for daily summary")
        return

    chat_summaries: list[tuple[str, str]] = []

    for group in groups:
        chat_id = group["chat_id"]
        chat_title = group.get("chat_title") or str(chat_id)
        try:
            msgs = await db.get_buffered_messages(chat_id, since=start_of_day)
            if not msgs:
                logger.info("=== No messages today in %s", chat_title)
                continue

            summary = await summarize_messages(msgs, ai_client=ai_client)
            await bot.send_message(chat_id, f"#summary\n📋 <b>{chat_title}</b>\n\n{summary}")
            chat_summaries.append((chat_title, summary))
            logger.info("=== Summarized group: %s (%d messages)", chat_title, len(msgs))
        except Exception as e:
            logger.error("=== Error summarizing group %s: %s", chat_title, e, exc_info=True)

    # Daily overview if multiple groups
    if len(chat_summaries) > 1:
        try:
            overview = await build_daily_overview(chat_summaries, ai_client=ai_client)
            # Send overview to all groups
            for group in groups:
                try:
                    await bot.send_message(
                        group["chat_id"],
                        f"#summary\n📊 <b>Обзор дня</b>\n\n{overview}",
                    )
                except Exception:
                    pass
        except Exception as e:
            logger.error("=== Error building daily overview: %s", e, exc_info=True)

    # Cleanup old messages
    deleted = await db.cleanup_old_messages(days=7)
    if deleted:
        logger.info("=== Cleaned up %d old messages", deleted)
