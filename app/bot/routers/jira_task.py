import logging
import re

from aiogram import Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.services.jira_client import JiraClient

logger = logging.getLogger("arkadyjarvis")
router = Router()


class CreateTask(StatesGroup):
    waiting_for_input = State()


@router.message(CreateTask.waiting_for_input)
async def handle_task_fsm(message: Message, state: FSMContext, db_user: DbUser, bitrix):
    text = (message.text or "").strip()
    if not text:
        await message.reply("Напиши задачу текстом: <code>DC Описание задачи</code>")
        return
    await state.clear()

    reply_text = ""
    if message.reply_to_message and message.reply_to_message.text:
        reply_text = message.reply_to_message.text.strip()

    await _create_task(message, text, reply_text, db_user=db_user, bitrix=bitrix)


async def _create_task(message: Message, body: str, reply_text: str, *, db_user: DbUser, bitrix):
    try:
        key_match = re.search(r"\b([A-Z][A-Z0-9]{1,9})\b", body)
        if not key_match:
            await message.reply("❌ Укажи проект: <code>DC Сделать landing page</code>")
            return
        project_key = key_match.group(1)

        inline_desc = body[key_match.end():].strip()

        full_text = "\n".join(filter(None, [inline_desc, reply_text]))
        if not full_text:
            await message.reply(
                "❌ Укажи описание задачи:\n"
                "<code>DC Сделать landing page</code>\n"
                "Или реплайни на сообщение с текстом задачи."
            )
            return

        short = full_text.split("\n")[0].split(". ")[0]
        summary = short[:100] if len(short) > 100 else short
        description = full_text

        user_email = await bitrix.get_user_email(db_user["bitrix_user_id"])

        async with JiraClient() as jira:
            jira_username = None
            if user_email:
                jira_username = await jira.find_user_by_email(user_email)

            result = await jira.create_issue(
                project_key, summary, description,
                reporter_name=jira_username,
                assignee_name=jira_username,
            )

        issue_key = result["key"]
        jira_base = settings.jira_url.rstrip("/")
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
