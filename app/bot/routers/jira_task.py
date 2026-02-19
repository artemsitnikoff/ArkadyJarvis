import logging
import re

from aiogram import F, Router
from aiogram.types import Message

from app import db
from app.bot.routers.start import MENU_KB
from app.services.jira_client import JiraClient

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(F.text.regexp(r"(?i)^(сделай|создай)\s+задачу"))
async def handle_create_task(message: Message):
    text = message.text or ""
    tg_id = message.from_user.id
    logger.info("*** TRIGGER: 'создай задачу' in chat=%s from user=%s", message.chat.id, tg_id)
    try:
        body = re.sub(r"(?i)^(сделай|создай)\s+задачу\s*", "", text).strip()
        key_match = re.search(r"\b([A-Z][A-Z0-9]{1,9})\b", body)
        if not key_match:
            await message.reply("❌ Укажи проект: <code>создай задачу DC Описание</code>")
            return
        project_key = key_match.group(1)

        # Text after project key = inline description
        inline_desc = body[key_match.end():].strip()

        # Reply message as fallback/addition
        reply_text = ""
        if message.reply_to_message and message.reply_to_message.text:
            reply_text = message.reply_to_message.text.strip()

        # Combine: inline first, then reply
        full_text = "\n".join(filter(None, [inline_desc, reply_text]))
        if not full_text:
            await message.reply(
                "❌ Укажи описание задачи:\n"
                "<code>создай задачу DC Сделать landing page</code>\n"
                "Или реплайни на сообщение с текстом задачи."
            )
            return

        short = full_text.split("\n")[0].split(". ")[0]
        summary = short[:100] if len(short) > 100 else short
        description = full_text

        async with JiraClient(tg_id) as jira:
            result = await jira.create_issue(project_key, summary, description)

        issue_key = result["key"]
        creds = await db.get_jira_credentials(tg_id)
        jira_base = creds["jira_url"].rstrip("/") if creds else ""
        await message.reply(
            f"✅ Задача создана: {issue_key}\n"
            f"📝 {summary}\n"
            f"🔗 {jira_base}/browse/{issue_key}",
            reply_markup=MENU_KB,
        )
        logger.info("*** Jira issue created: %s", issue_key)
    except Exception as e:
        logger.error("*** ERROR creating Jira issue: %s", e, exc_info=True)
        await message.reply(f"❌ Ошибка создания задачи: {e}")
