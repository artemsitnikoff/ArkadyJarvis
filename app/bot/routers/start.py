import html as html_mod
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
from app.db import DbUser
from app.version import __version__

logger = logging.getLogger("arkadyjarvis")
router = Router()

MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [
        InlineKeyboardButton(text="👤 Сотрудник", callback_data="hint:employee"),
        InlineKeyboardButton(text="👥 Моя команда", callback_data="hint:team"),
    ],
    [
        InlineKeyboardButton(text="📅 Встреча", callback_data="hint:meeting"),
        InlineKeyboardButton(text="🕐 Найди время", callback_data="hint:freetime"),
    ],
    [
        InlineKeyboardButton(text="📝 Задача", callback_data="hint:task"),
        InlineKeyboardButton(text="💼 Лид", callback_data="hint:lead"),
    ],
    [
        InlineKeyboardButton(text="📋 Мои встречи", callback_data="hint:meetings"),
        InlineKeyboardButton(text="🎨 Картинка", callback_data="hint:image"),
    ],
    [
        InlineKeyboardButton(text="🧠 Спроси AI", callback_data="hint:askai"),
        InlineKeyboardButton(text="📊 Суммаризация", callback_data="hint:summary"),
    ],
    [
        InlineKeyboardButton(text="📄 Проверь договор", callback_data="hint:contract"),
        InlineKeyboardButton(text="⚖️ Цицерон", callback_data="hint:cicero"),
    ],
    [
        InlineKeyboardButton(text="🎓 Сократ", callback_data="hint:socrates"),
        InlineKeyboardButton(text="🕵️ Штирлиц", callback_data="hint:stirlitz"),
    ],
    [
        InlineKeyboardButton(text="🤖 Марфа", callback_data="hint:glafira"),
        InlineKeyboardButton(text="👔 Глафира", callback_data="hint:recruiter"),
    ],
    [
        InlineKeyboardButton(text="🏠 Мисис Хадсон", callback_data="hint:hudson"),
        InlineKeyboardButton(text="❓ Все команды", callback_data="hint:all"),
    ],
])

BACK_MENU_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")],
])

HELP_TEXT = (
    "📖 <b>Как пользоваться</b>\n\n"
    "Всё работает через кнопки меню — жми нужную, бот подскажет что делать дальше.\n\n"

    "<b>🕘 Рабочий день</b>\n"
    "🏢 Начать день в офисе / 🏠 удалённо — открывает таймер в Bitrix24\n"
    "👤 Сотрудник — найти коллегу по имени (карточка с контактами)\n"
    "👥 Моя команда — список команды со статусами «работает / пауза / не начал»\n\n"

    "<b>📅 Календарь и задачи</b>\n"
    "📅 Встреча — создать встречу в Bitrix24 (время, дата, участники)\n"
    "🕐 Найди время — свободные слоты по календарям коллег на 5 рабочих дней\n"
    "📋 Мои встречи — сегодняшние встречи со ссылками в Bitrix\n"
    "📝 Задача — тикет в Jira (опиши сумбурно — AI оформит по шаблону)\n"
    "💼 Лид — CRM-лид в Bitrix24 (текстом или голосовым 🎤 — расшифрую и разберу)\n\n"

    "<b>🧠 AI-инструменты</b>\n"
    "🎨 Картинка — сгенерировать изображение по описанию (можно редактировать фото)\n"
    "🧠 Спроси AI — свободный вопрос к Claude\n"
    "📊 Суммаризация — отчёт по рабочим чатам за сегодня\n\n"

    "<b>📄 Документы</b>\n"
    "📄 Проверь договор — PDF/DOCX → AI сверит по чек-листу (реквизиты, НДС, приёмка)\n"
    "⚖️ Цицерон — AI-юрист (ГК, КоАП, АПК, НК РФ, КонсультантПлюс), принимает документы\n"
    "🎓 Сократ — расшифровка встречи + ревью + экспертиза (ссылка на запись)\n\n"

    "<b>🤖 AI-сотрудники</b>\n"
    "🤖 Марфа — AI офис-менеджер через браузер (ограниченный доступ)\n"
    "👔 Глафира — AI-рекрутёр, оценивает кандидатов в Potok.io (ограниченный доступ)\n\n"

    "<b>⚙️ Slash-команды</b>\n"
    "<code>/start</code> — авторизация в боте через Bitrix\n"
    "<code>/help</code> — это сообщение\n"
    "<code>/summary</code> — саммари текущего группового чата за сегодня"
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


@router.callback_query(F.data == "noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data.startswith("work:"))
async def handle_work(
    callback: CallbackQuery, bitrix, ai_client, db_user: DbUser | None = None,
):
    from app.bot.routers.work import start_work_day
    await start_work_day(callback, bitrix, ai_client, db_user)


def _simple_fsm_hints() -> dict[str, tuple]:
    """(state, prompt_text, initial_fsm_data | None) for button-only FSM entries.

    Lazy-loaded inside the handler to avoid importing every router at module
    import time (each router imports from start, so top-level imports would
    create cycles).
    """
    from app.bot.routers.ask_ai import AskAI
    from app.bot.routers.cicero import Cicero
    from app.bot.routers.contract import ContractCheck
    from app.bot.routers.employee import FindEmployee
    from app.bot.routers.free_slots import BookSlot
    from app.bot.routers.image import ImageGen
    from app.bot.routers.jira_task import CreateTask
    from app.bot.routers.lead import CreateLead
    from app.bot.routers.meeting import MeetingSetup
    from app.bot.routers.socrates import Socrates
    from app.bot.routers.stirlitz import Stirlitz

    return {
        "employee": (
            FindEmployee.waiting_for_name,
            "👤 <b>Найди сотрудника</b>\n\nНапиши имя или фамилию:",
            None,
        ),
        "meeting": (
            MeetingSetup.waiting_for_command,
            "📅 <b>Создать встречу</b>\n\n"
            "Напиши время и участников:\n"
            "<code>14:00 @nick1 @nick2</code>\n\n"
            "Или просто время — найду коллег по имени.",
            None,
        ),
        "freetime": (
            BookSlot.searching_attendee,
            "🕐 <b>Найди время</b>\n\nНапиши имя или фамилию коллеги:",
            {"attendee_ids": [], "attendee_names": []},
        ),
        "task": (
            CreateTask.waiting_for_input,
            "📝 <b>Задача Jira</b>\n\n"
            "Опиши задачу своими словами: что делаем, для кого, к какому сроку, "
            "какие блокеры. Я переформулирую по нашему шаблону "
            "(Задача, Приоритет, Контекст, Что сделать, Блокеры, Ожидаемый результат, "
            "Ориентир начала работ) и создам тикет в Jira.\n\n"
            "Формат:\n"
            "<code>DC &lt;твоё описание&gt;</code>\n\n"
            "Где <b>DC</b> — ключ проекта в Jira.",
            None,
        ),
        "lead": (
            CreateLead.waiting_for_info,
            "💼 <b>Создать лид</b>\n\n"
            "Напиши данные контакта (имя, компания, телефон, email) "
            "или запиши голосовое 🎤 — расшифрую и сам разберу поля.",
            None,
        ),
        "image": (
            ImageGen.waiting_for_prompt,
            "🎨 Напиши что нарисовать:",
            None,
        ),
        "askai": (
            AskAI.waiting_for_question,
            "🧠 Задай вопрос:",
            None,
        ),
        "contract": (
            ContractCheck.waiting_for_document,
            "📄 <b>Проверка договора</b>\n\n"
            "Пришли файл (PDF, DOCX или TXT) — проверю по правилам "
            "и выдам список несоответствий.",
            None,
        ),
        "cicero": (
            Cicero.chatting,
            "⚖️ <b>Цицерон</b> — юридический консультант\n\n"
            "Задай вопрос текстом или приложи документ (PDF/DOCX/TXT) "
            "с вопросом в подписи. Отвечу по российскому законодательству "
            "(ГК, КоАП, АПК, НК РФ, КонсультантПлюс).\n\n"
            "Можно задавать вопросы подряд. Выход — «◀️ Меню».",
            None,
        ),
        "socrates": (
            Socrates.waiting_for_url,
            "🎓 <b>Сократ</b> — ассистент аналитика\n\n"
            "Пришли <b>ссылку</b> на запись встречи (Яндекс.Диск / Telemost / "
            "прямой URL). Я скачаю её, сожму, расшифрую с диаризацией и дам:\n"
            "1. Транскрипт\n"
            "2. Ревью встречи (договорённости, решения, открытые вопросы)\n"
            "3. Заготовку для аналитика — словарь, факты, черновик структуры ТЗ, "
            "план погружения в предметную область\n\n"
            "⚠️ Встречи длиннее 90 минут пока не обрабатываю. "
            "Файл-видео напрямую не принимаю (Telegram режет upload на 20 МБ).",
            None,
        ),
        "stirlitz": (
            Stirlitz.waiting_for_query,
            "🕵️ <b>Штирлиц</b> — разведка из открытых источников\n\n"
            "Введи один из вариантов:\n"
            "• <b>ИНН</b> (10 или 12 цифр) — точная карточка компании\n"
            "• <b>Название компании</b> — найду по ЕГРЮЛ\n"
            "• <b>ФИО человека</b> (можно с компанией/городом для точности) — "
            "соберу публичный портрет\n\n"
            "Источники: DaData (ЕГРЮЛ), ГИР БО ФНС (отчётность), WebSearch "
            "(новости, LinkedIn, Habr, ВК, конференции).",
            None,
        ),
    }


async def _enter_glafira(callback: CallbackQuery, state: FSMContext):
    from app.bot.routers.glafira import GLAFIRA_ALLOWED, GLAFIRA_EXIT_KB, Glafira

    if callback.from_user.id not in GLAFIRA_ALLOWED:
        await callback.message.answer(
            "🚧 Функция в тестовом режиме. Доступ ограничен.",
            reply_markup=MENU_KB,
        )
        await callback.answer()
        return
    await state.set_state(Glafira.chatting)
    await state.update_data(messages=[])
    await callback.message.answer(
        "🤖 <b>Марфа</b> — AI офис-менеджер\n\n"
        "Напиши что нужно сделать. Я управляю браузером "
        "и могу выполнять задачи на сайтах.\n\n"
        "Для выхода нажми «◀️ Меню».",
        reply_markup=GLAFIRA_EXIT_KB,
    )
    await callback.answer()


async def _enter_recruiter(callback: CallbackQuery, state: FSMContext, potok):
    from app.bot.routers.recruiter import RECRUITER_ALLOWED, Recruiter

    if callback.from_user.id not in RECRUITER_ALLOWED:
        await callback.message.answer(
            "🚧 Функция в тестовом режиме. Доступ ограничен.",
            reply_markup=MENU_KB,
        )
        await callback.answer()
        return
    await callback.message.answer(
        "👔 <b>Глафира</b> — AI-рекрутёр\n\n"
        "Оцениваю кандидатов по вакансиям из Potok.io: сравниваю резюме "
        "с описанием вакансии через Claude, ставлю балл 0–100, выделяю "
        "сильные и слабые стороны. Результат публикую комментарием в Potok "
        "и добавляю префикс со скором к фамилии кандидата для сортировки.\n\n"
        "Сейчас подтяну список вакансий — выбери нужную, "
        "и я начну оценку резюме кандидатов."
    )
    wait = await callback.message.answer("👔 Загружаю вакансии...")
    try:
        jobs = await potok.get_jobs()
    except Exception as e:
        logger.error("Potok error: %s", e, exc_info=True)
        await wait.edit_text(f"❌ Potok недоступен: {e}", reply_markup=MENU_KB)
        await callback.answer()
        return
    if not jobs:
        await wait.edit_text("👔 Нет активных вакансий.", reply_markup=MENU_KB)
        await callback.answer()
        return
    buttons = [
        [InlineKeyboardButton(
            text=f"{j.name} ({j.total_applicants})",
            callback_data=f"recruit:job:{j.id}",
        )]
        for j in jobs[:20]
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="recruit:exit")])
    await wait.edit_text(
        "👔 Выбери вакансию для оценки кандидатов:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
    await state.set_state(Recruiter.choosing_job)
    await callback.answer()


@router.callback_query(F.data.startswith("hint:"))
async def handle_hint(
    callback: CallbackQuery, state: FSMContext, bitrix, potok, ai_client, bot,
    openrouter, db_user: DbUser | None = None,
):
    key = callback.data.split(":", 1)[1]

    # Specialized handlers that need service deps or extra logic.
    if key == "team":
        await _show_team(callback, bitrix, db_user=db_user)
        return
    if key == "meetings":
        await _show_meetings(callback, bitrix, db_user=db_user)
        return
    if key == "summary":
        await callback.answer()
        await _run_summary(callback, ai_client, bot=bot, db_user=db_user)
        return
    if key == "glafira":
        await _enter_glafira(callback, state)
        return
    if key == "recruiter":
        await _enter_recruiter(callback, state, potok)
        return
    if key == "hudson":
        from app.bot.routers.hudson import enter_hudson
        await enter_hudson(callback, openrouter, bitrix=bitrix)
        return

    # Data-driven FSM-entry hints (set state + show prompt).
    simple = _simple_fsm_hints()
    if key in simple:
        fsm_state, text, init_data = simple[key]
        await state.set_state(fsm_state)
        if init_data:
            await state.update_data(**init_data)
        await callback.message.answer(text, reply_markup=BACK_MENU_KB)
        await callback.answer()
        return

    # Info-only: "all" shows the full help, anything else is an unknown key.
    if key == "all":
        text = f"{HELP_TEXT}\n\n<i>v{__version__}</i>"
    else:
        text = "🤷 Неизвестная команда"
    await callback.message.answer(text, reply_markup=BACK_MENU_KB)
    await callback.answer()


async def _run_summary(
    callback: CallbackQuery, ai_client, bot=None, *, db_user: DbUser | None = None,
):
    """Run summarization. In DM: overview of all groups. In group: summarize current chat."""
    from zoneinfo import ZoneInfo

    from app.summarizer import build_daily_overview, summarize_from_buffer, summarize_messages

    await callback.answer()
    tz = ZoneInfo(settings.timezone)
    start_of_day = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)

    try:
        if callback.message.chat.type in ("group", "supergroup"):
            summary = await summarize_from_buffer(
                callback.message.chat.id, ai_client=ai_client, since=start_of_day,
            )
            await callback.message.answer(f"📊 #summary\n\n{summary}", reply_markup=MENU_KB)
        else:
            # DM: summarize all groups the user belongs to
            wait_msg = await callback.message.answer("📊 Собираю обзор дня...")
            groups = await db.get_all_group_chats()
            tg_id = callback.from_user.id
            user_summaries: list[tuple[str, str]] = []

            for group in groups:
                chat_id = group["chat_id"]
                chat_title = group.get("chat_title") or str(chat_id)
                msgs = await db.get_buffered_messages(chat_id, since=start_of_day)
                if not msgs:
                    continue
                if bot:
                    try:
                        member = await bot.get_chat_member(chat_id, tg_id)
                        if member.status in ("left", "kicked"):
                            continue
                    except Exception:
                        pass
                summary = await summarize_messages(msgs, ai_client=ai_client)
                user_summaries.append((chat_title, summary))

            if not user_summaries:
                await wait_msg.edit_text("Нет сообщений в группах за сегодня.", reply_markup=MENU_KB)
                return

            user_name = db_user.get("display_name", "") if db_user else ""
            overview = await build_daily_overview(
                user_summaries, ai_client=ai_client, user_name=user_name,
            )
            await wait_msg.edit_text(
                f"#summary\n📊 <b>Обзор дня</b>\n\n{overview}", reply_markup=MENU_KB,
            )
    except Exception:
        logger.error("Summary error", exc_info=True)
        await callback.message.answer(
            "❌ Не удалось собрать сводку. Попробуй ещё раз.", reply_markup=MENU_KB,
        )


def _work_status_line(person: dict) -> str:
    """Format one team member line with work status indicator."""
    name = html_mod.escape(person["name"])
    pos = html_mod.escape(person.get("position", ""))
    status = person.get("work_status", "")
    start = person.get("work_start", "")

    if status == "OPENED":
        icon = "\U0001f7e2"  # green circle
        time_str = ""
        if start:
            try:
                # Try ISO format: 2024-03-15T09:00:00+07:00
                if "T" in start:
                    dt = datetime.fromisoformat(start)
                else:
                    dt = datetime.strptime(start, "%d.%m.%Y %H:%M:%S")
                time_str = f" (с {dt.strftime('%H:%M')})"
            except (ValueError, TypeError):
                pass
        label = f"{icon} <b>{name}</b>"
        if pos:
            label += f" — {pos}"
        label += time_str
    elif status == "PAUSED":
        label = f"\U0001f7e1 <b>{name}</b>"  # yellow circle
        if pos:
            label += f" — {pos}"
        label += " (пауза)"
    else:
        label = f"\u26aa <b>{name}</b>"  # white circle
        if pos:
            label += f" — {pos}"

    return label


async def _show_team(
    callback: CallbackQuery, bitrix, *, db_user: DbUser | None = None,
):
    if not db_user or not db_user.get("bitrix_user_id"):
        await callback.message.answer("❌ Сначала авторизуйся: /start")
        await callback.answer()
        return

    await callback.answer()
    wait_msg = await callback.message.answer("👥 Загружаю команду...")

    try:
        team = await bitrix.get_my_team(db_user["bitrix_user_id"])
    except Exception as e:
        logger.error("Failed to fetch team: %s", e, exc_info=True)
        await wait_msg.edit_text("❌ Не удалось загрузить команду", reply_markup=MENU_KB)
        return

    if not team:
        await wait_msg.edit_text("❌ Информация о команде недоступна", reply_markup=MENU_KB)
        return

    dept = html_mod.escape(team.get("department", ""))
    lines = [f"👥 <b>Моя команда</b> — {dept}"] if dept else ["👥 <b>Моя команда</b>"]

    # Supervisor
    if team.get("supervisor"):
        sup = team["supervisor"]
        lines.append(f"\n👆 <b>Руководитель:</b> {_work_status_line(sup)}")

    # Colleagues (for regular employees) or Subordinates (for managers)
    if team.get("is_head") and team.get("subordinates"):
        lines.append("\n👇 <b>Подчинённые:</b>")
        for p in team["subordinates"]:
            lines.append(_work_status_line(p))
        if team.get("colleagues"):
            lines.append("\n👥 <b>Коллеги (руководители):</b>")
            for p in team["colleagues"]:
                lines.append(_work_status_line(p))
    elif team.get("colleagues"):
        lines.append("\n👥 <b>Коллеги:</b>")
        for p in team["colleagues"]:
            lines.append(_work_status_line(p))

    if not team.get("supervisor") and not team.get("colleagues") and not team.get("subordinates"):
        lines.append("\nНет данных о команде")

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")],
    ])
    await wait_msg.edit_text("\n".join(lines), reply_markup=kb)


async def _show_meetings(
    callback: CallbackQuery, bitrix, *, db_user: DbUser | None = None,
):
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
async def handle_back_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Выбери команду — покажу подсказку:",
        reply_markup=MENU_KB,
    )
    await callback.answer()
