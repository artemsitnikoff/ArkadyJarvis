import logging

from aiogram import Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message

from app import db
from app.config import settings

logger = logging.getLogger("arkadyjarvis")
router = Router()


class JiraSetup(StatesGroup):
    username = State()
    password = State()


@router.message(Command("jira"))
async def cmd_jira(message: Message, state: FSMContext):
    if not settings.jira_url:
        await message.answer("Jira URL не настроен в конфигурации бота.")
        return
    await state.set_state(JiraSetup.username)
    await message.answer(
        f"Настройка Jira ({settings.jira_url}).\n\n"
        "Шаг 1/2: отправь логин (username)",
    )


@router.message(StateFilter(JiraSetup.username))
async def jira_username(message: Message, state: FSMContext):
    await state.update_data(jira_username=message.text.strip())
    await state.set_state(JiraSetup.password)
    await message.answer("Шаг 2/2: отправь пароль")


@router.message(StateFilter(JiraSetup.password))
async def jira_password(message: Message, state: FSMContext):
    password = message.text.strip()
    try:
        await message.delete()
    except Exception:
        logger.warning("Could not delete password message for user %s", message.from_user.id)
    data = await state.get_data()
    await db.save_jira_credentials(
        telegram_id=message.from_user.id,
        jira_url=settings.jira_url,
        jira_username=data["jira_username"],
        jira_password=password,
    )
    await state.clear()
    await message.answer("Jira настроена! Теперь можешь создавать задачи.")
    logger.info("Jira credentials saved for user %s", message.from_user.id)


@router.message(Command("skip"))
async def cmd_skip(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Настройка Jira пропущена. Можешь настроить позже через /jira")
