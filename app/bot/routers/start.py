import logging

from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app import db
from app.services.bitrix_client import BitrixClient

logger = logging.getLogger("arkadyjarvis")
router = Router()

MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="📊 Суммаризация", callback_data="hint:summary"),
        InlineKeyboardButton(text="📅 Встреча", callback_data="hint:meeting"),
    ],
    [
        InlineKeyboardButton(text="🕐 Найди время", callback_data="hint:freetime"),
        InlineKeyboardButton(text="📝 Задача", callback_data="hint:task"),
    ],
    [
        InlineKeyboardButton(text="💼 Лид", callback_data="hint:lead"),
        InlineKeyboardButton(text="❓ Все команды", callback_data="hint:all"),
    ],
])

COPY_TIP = "\n\n<i>👆 Нажми на команду — скопируется</i>"

HINTS = {
    "summary": (
        "📊 <b>Суммаризация</b>\n\n"
        "📋 <code>суммаризация</code> — отчёт за сегодня\n\n"
        "Напиши в любой чат где есть бот." + COPY_TIP
    ),
    "meeting": (
        "📅 <b>Встреча</b>\n\n"
        "📋 <code>создай встречу 14:00 @nick1 @nick2</code>\n"
        "📋 <code>создай встречу 16:00 25.02 @nick</code>\n\n"
        "Укажи время, дату (необязательно) и @ники участников." + COPY_TIP
    ),
    "freetime": (
        "🕐 <b>Найди время</b>\n\n"
        "📋 <code>найди время @nick1 @nick2</code>\n\n"
        "Покажет свободные слоты на 5 рабочих дней." + COPY_TIP
    ),
    "task": (
        "📝 <b>Задача Jira</b>\n\n"
        "📋 <code>создай задачу DC Сделать landing page</code>\n"
        "📋 <code>создай задачу DC</code> — реплай на сообщение\n\n"
        "DC — ключ проекта. Описание можно писать в команде или реплаем." + COPY_TIP
    ),
    "lead": (
        "💼 <b>Лид</b>\n\n"
        "📋 <code>создай лид Иванов, Рога и Копыта, +7999123</code>\n\n"
        "Бот сам разберёт имя, компанию и контакты." + COPY_TIP
    ),
}

HELP_TEXT = (
    "📖 <b>Как пользоваться</b>\n\n"
    "Нажми на команду — она скопируется. "
    "Вставь в чат, допиши детали и отправь.\n\n"
    "📊 <b>Суммаризация</b>\n"
    "📋 <code>суммаризация</code>\n\n"
    "📅 <b>Встреча</b>\n"
    "📋 <code>создай встречу 14:00 @nick1 @nick2</code>\n\n"
    "🕐 <b>Найди время</b>\n"
    "📋 <code>найди время @nick1 @nick2</code>\n\n"
    "📝 <b>Задача Jira</b>\n"
    "📋 <code>создай задачу DC Описание</code>\n\n"
    "💼 <b>Лид</b>\n"
    "📋 <code>создай лид Иванов, Рога и Копыта, +7999123</code>\n\n"
    "⚙️ <b>Jira</b> — /jira в личке бота"
)


@router.message(CommandStart())
async def cmd_start(message: Message):
    user = await db.get_user(message.from_user.id)
    if user and user.get("bitrix_user_id"):
        await message.answer(
            f"✅ Ты авторизован как <b>{user['display_name']}</b>.\n\n"
            "Выбери команду — покажу подсказку:",
            reply_markup=MENU_KB,
        )
        return

    username = message.from_user.username
    if not username:
        await message.answer(
            "❌ У тебя не задан username в Telegram.\n"
            "Установи его в настройках Telegram и попробуй снова.",
        )
        return

    bitrix = BitrixClient.get()
    bitrix_id, full_name = await bitrix.find_user_by_nickname(username)

    if not bitrix_id:
        await message.answer(
            f"❌ Не нашёл пользователя с ником @{username} в Bitrix24.\n"
            "Проверь, что ник указан в твоём профиле Bitrix "
            "(поле Telegram).",
        )
        return

    await db.upsert_user(
        telegram_id=message.from_user.id,
        bitrix_user_id=bitrix_id,
        display_name=full_name,
    )

    logger.info(
        "User authorized: tg=%s @%s → bitrix=%s (%s)",
        message.from_user.id, username, bitrix_id, full_name,
    )
    await message.answer(
        f"✅ Ты авторизован как <b>{full_name}</b>\n\n"
        "Выбери команду — покажу подсказку:",
        reply_markup=MENU_KB,
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(HELP_TEXT, reply_markup=MENU_KB)


@router.callback_query(F.data.startswith("hint:"))
async def handle_hint(callback: CallbackQuery):
    key = callback.data.split(":", 1)[1]
    if key == "all":
        text = HELP_TEXT
    else:
        text = HINTS.get(key, "🤷 Неизвестная команда")
    await callback.message.answer(text)
    await callback.answer()
