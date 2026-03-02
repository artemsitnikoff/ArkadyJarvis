import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.filters import CommandStart, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    ReplyKeyboardRemove,
)

from app import db
from app.config import settings
from app.version import __version__

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
        InlineKeyboardButton(text="📋 Мои встречи", callback_data="hint:meetings"),
    ],
    [
        InlineKeyboardButton(text="🎨 Картинка", callback_data="hint:image"),
        InlineKeyboardButton(text="🧠 Спроси AI", callback_data="hint:askai"),
    ],
    [
        InlineKeyboardButton(text="❓ Все команды", callback_data="hint:all"),
    ],
])

BACK_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")],
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
        "Укажи время, дату (необязательно) и @ники участников.\n"
        "Не помнишь @ники? Напиши в личку бота — найду по имени." + COPY_TIP
    ),
    "freetime": (
        "🕐 <b>Найди время</b>\n\n"
        "📋 <code>найди время @nick1 @nick2</code>\n\n"
        "Покажет свободные слоты на 5 рабочих дней.\n"
        "Не помнишь @ники? Напиши в личку бота — найду по имени." + COPY_TIP
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
    "image": (
        "🎨 <b>Картинка</b>\n\n"
        "📋 <code>нарисуй кота в космосе</code>\n\n"
        "Можно отправить фото с подписью — AI переделает картинку.\n"
        "Или нажми кнопку и напиши промпт / отправь фото." + COPY_TIP
    ),
    "askai": (
        "🧠 <b>Спроси AI</b>\n\n"
        "📋 <code>спроси что такое микросервисы</code>\n\n"
        "Ответит Claude Opus 4.6." + COPY_TIP
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
    "🎨 <b>Картинка</b>\n"
    "📋 <code>нарисуй кота в космосе</code>\n\n"
    "🧠 <b>Спроси AI</b>\n"
    "📋 <code>спроси что такое микросервисы</code>"
)


@router.message(CommandStart())
async def cmd_start(message: Message, bitrix):
    # Remove old ReplyKeyboard if any
    await message.answer("⏳", reply_markup=ReplyKeyboardRemove())

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
async def handle_hint(callback: CallbackQuery, state: FSMContext, bitrix):
    key = callback.data.split(":", 1)[1]

    if key == "image":
        from app.bot.routers.image import ImageGen
        await state.set_state(ImageGen.waiting_for_prompt)
        await callback.message.answer("🎨 Напиши что нарисовать:", reply_markup=BACK_MENU_KB)
        await callback.answer()
        return

    if key == "askai":
        from app.bot.routers.ask_ai import AskAI
        await state.set_state(AskAI.waiting_for_question)
        await callback.message.answer("🧠 Задай вопрос:", reply_markup=BACK_MENU_KB)
        await callback.answer()
        return

    if key == "meetings":
        await _show_meetings(callback, bitrix)
        return

    if key == "all":
        text = f"{HELP_TEXT}\n\n<i>v{__version__}</i>"
    else:
        text = HINTS.get(key, "🤷 Неизвестная команда")
    await callback.message.answer(text, reply_markup=BACK_MENU_KB)
    await callback.answer()


async def _show_meetings(callback: CallbackQuery, bitrix):
    db_user = await db.get_user(callback.from_user.id)
    if not db_user or not db_user.get("bitrix_user_id"):
        await callback.message.answer("❌ Сначала авторизуйся: /start")
        await callback.answer()
        return

    try:
        events = await bitrix.get_user_events(db_user["bitrix_user_id"])
    except Exception as e:
        logger.error("Failed to fetch meetings: %s", e)
        await callback.message.answer("❌ Не удалось загрузить встречи")
        await callback.answer()
        return

    if not events:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")],
        ])
        await callback.message.answer(
            "📋 <b>Мои встречи</b>\n\nНет встреч на сегодня",
            reply_markup=kb,
        )
        await callback.answer()
        return

    domain = db_user.get("bitrix_domain") or settings.bitrix_domain
    uid = db_user["bitrix_user_id"]
    buttons = []
    for ev in events:
        try:
            dt = datetime.strptime(ev["date_from"], "%d.%m.%Y %H:%M:%S")
            time_str = dt.strftime("%H:%M")
        except (ValueError, KeyError):
            time_str = "??:??"
        name = ev["name"]
        label = f"{time_str} {name}"
        if len(label) > 45:
            label = label[:42] + "..."
        url = f"https://{domain}/company/personal/user/{uid}/calendar/?EVENT_ID={ev['id']}"
        buttons.append([InlineKeyboardButton(text=label, url=url)])

    buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer("📋 <b>Мои встречи</b>", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "back:menu")
async def handle_back_menu(callback: CallbackQuery):
    await callback.message.answer(
        "Выбери команду — покажу подсказку:",
        reply_markup=MENU_KB,
    )
    await callback.answer()
