import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.types import BufferedInputFile

from app import db
from app.config import settings
from app.services.ai_client import AIClient
from app.services.openrouter_client import OpenRouterClient
from app.services.prompts import load_prompt
from app.summarizer import build_daily_overview, summarize_messages

logger = logging.getLogger("arkadyjarvis")


async def daily_summary_job(bot: Bot, ai_client: AIClient):
    """Summarize each group chat and send daily overview to all active users via DM."""
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    groups = await db.get_all_group_chats()
    if not groups:
        logger.info("=== No group chats for daily summary")
        return

    # chat_id -> (title, summary)
    chat_summaries: dict[int, tuple[str, str]] = {}

    for group in groups:
        chat_id = group["chat_id"]
        chat_title = group.get("chat_title") or str(chat_id)
        try:
            msgs = await db.get_buffered_messages(chat_id, since=start_of_day)
            if not msgs:
                logger.info("=== No messages today in %s", chat_title)
                continue

            summary = await summarize_messages(msgs, ai_client=ai_client)
            chat_summaries[chat_id] = (chat_title, summary)
            logger.info("=== Summarized group: %s (%d messages)", chat_title, len(msgs))
        except Exception as e:
            logger.error("=== Error summarizing group %s: %s", chat_title, e, exc_info=True)

    # Build personalized overview per user (only groups they belong to)
    if chat_summaries:
        users = await db.get_active_users()
        for user in users:
            tg_id = user["telegram_id"]
            # Filter summaries to groups this user is a member of
            user_summaries: list[tuple[str, str]] = []
            for chat_id, (title, summary) in chat_summaries.items():
                try:
                    member = await bot.get_chat_member(chat_id, tg_id)
                    if member.status not in ("left", "kicked"):
                        user_summaries.append((title, summary))
                except Exception:
                    pass  # bot can't check membership — skip this group

            if not user_summaries:
                continue

            try:
                overview = await build_daily_overview(
                    user_summaries, ai_client=ai_client,
                    user_name=user.get("display_name", ""),
                )
                await bot.send_message(
                    tg_id,
                    f"#summary\n📊 <b>Обзор дня</b>\n\n{overview}",
                )
            except Exception as e:
                logger.warning(
                    "=== Could not send overview to user %s: %s", tg_id, e,
                )
        logger.info("=== Daily overview sent to users")

    # Cleanup old messages
    deleted = await db.cleanup_old_messages(days=7)
    if deleted:
        logger.info("=== Cleaned up %d old messages", deleted)


async def wednesday_frog_job(bot: Bot, ai_client: AIClient, openrouter: OpenRouterClient):
    """Generate and send a frog meme image to the configured chat every Wednesday."""
    chat_id = settings.wednesday_frog_chat_id
    if not chat_id:
        logger.info("=== wednesday_frog_job skipped: WEDNESDAY_FROG_CHAT_ID not set")
        return

    try:
        meta_prompt = load_prompt("wednesday_frog")
        image_prompt = (await ai_client.complete(meta_prompt)).strip()
        logger.info("=== Wednesday frog prompt: %s", image_prompt)

        image_bytes = await openrouter.generate_image(image_prompt)
        photo = BufferedInputFile(image_bytes, filename="wednesday_frog.png")
        await bot.send_photo(chat_id, photo, caption="🐸 Со средой, мои чуваки!")
        logger.info("=== Wednesday frog sent to chat %s", chat_id)
    except Exception as e:
        logger.error("=== wednesday_frog_job failed: %s", e, exc_info=True)
