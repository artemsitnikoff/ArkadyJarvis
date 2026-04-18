import asyncio
import html as html_mod
import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app import db
from app.db import DbUser
from app.utils import parse_attendees, parse_meeting_time

logger = logging.getLogger("arkadyjarvis")
router = Router()


# ── FSM states ────────────────────────────────────────────────

class MeetingSetup(StatesGroup):
    waiting_for_command = State()
    searching_attendee = State()
    waiting_for_title = State()


# ── Keyboards ────────────────────────────────────────────────

def _cancel_kb(show_add_me: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if show_add_me:
        rows.append([InlineKeyboardButton(text="+ Я", callback_data="search:addme")])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mtg:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _search_status_kb(attendee_names: list[str], show_add_me: bool = True) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    first_row = [
        InlineKeyboardButton(text="+ Ещё участник", callback_data="search:more"),
        InlineKeyboardButton(text="📅 Создать встречу", callback_data="search:done"),
    ]
    if show_add_me:
        first_row.insert(0, InlineKeyboardButton(text="+ Я", callback_data="search:addme"))
    rows.append(first_row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mtg:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _search_results_kb(users: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for u in users:
        rows.append([InlineKeyboardButton(
            text=u["name"],
            callback_data=f"pick:{u['id']}:{u['name'][:40]}",
        )])
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="mtg:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ── Helpers ───────────────────────────────────────────────────

def _build_meeting_reply(
    dt: datetime,
    event_id,
    bitrix_url: str,
    *,
    found_names: list[str] | None = None,
    external_emails: list[str] | None = None,
    invite_emails: list[str] | None = None,
    not_found: list[str] | None = None,
    context: str = "",
) -> str:
    esc = html_mod.escape
    text = f"✅ Встреча создана: {dt:%d.%m.%Y} в {dt:%H:%M} (id: {esc(str(event_id))})\n🔗 {bitrix_url}"
    if found_names:
        text += f"\n👥 Участники: {esc(', '.join(found_names))}"
    if external_emails:
        text += f"\n👥 По email: {esc(', '.join(external_emails))}"
    if invite_emails:
        text += f"\n📧 В описании (пригласить вручную): {esc(', '.join(invite_emails))}"
    if not_found:
        text += f"\n⚠️ Не найден: {esc(', '.join(not_found))}"
    if context:
        text += f"\n📝 {esc(context)}"
    return text


async def _do_create_meeting(
    message: Message,
    db_user: DbUser,
    bitrix,
    dt: datetime,
    context: str,
    attendee_ids: list[int],
    attendee_names: list[str],
):
    """Create a Bitrix meeting and send confirmation message."""
    title = context[:80] if context else "Встреча"
    description = context or ""

    owner_user_id = db_user["bitrix_user_id"]
    result = await bitrix.create_meeting(
        title=title,
        date=dt,
        owner_user_id=owner_user_id,
        description=description,
        attendee_ids=attendee_ids if attendee_ids else None,
    )

    event_id = result.get("id", "?")
    bitrix_url = f"https://{settings.bitrix_domain}/company/personal/user/{owner_user_id}/calendar/?EVENT_ID={event_id}"

    reply_text = _build_meeting_reply(
        dt, event_id, bitrix_url,
        found_names=attendee_names,
        context=context,
    )
    await message.reply(reply_text, reply_markup=MENU_KB)


# ── Handlers ──────────────────────────────────────────────────

@router.message(MeetingSetup.waiting_for_command)
async def handle_meeting_fsm(message: Message, state: FSMContext, db_user: DbUser, bitrix):
    """FSM handler: user entered meeting details from button (no command prefix)."""
    text = (message.text or "").strip()
    if not text:
        await message.reply("Напиши время и участников, например:\n<code>14:00 @nick1 @nick2</code>")
        return

    # Prepend fake prefix so parse_meeting_time works
    fake_text = f"создай встречу {text}"
    dt, err = parse_meeting_time(fake_text)
    if err:
        await message.reply(err)
        return

    await state.clear()

    context = ""
    if message.reply_to_message and message.reply_to_message.text:
        context = message.reply_to_message.text

    nicknames, emails = parse_attendees(fake_text)

    if not nicknames and not emails:
        await state.update_data(
            dt=dt.isoformat(),
            context=context,
            attendee_ids=[],
            attendee_names=[],
        )
        await state.set_state(MeetingSetup.searching_attendee)
        await message.reply(
            "Напиши имя или фамилию коллеги:",
            reply_markup=_cancel_kb(show_add_me=True),
        )
        return

    # Has @nicks/emails — proceed with full creation (same as text trigger)
    await _create_meeting_with_nicks(message, state, db_user, bitrix, dt, context, nicknames, emails)


async def _create_meeting_with_nicks(
    message, state, db_user, bitrix, dt, context, nicknames, emails,
):
    """Resolve @nicks and emails, create meeting in Bitrix."""
    attendee_ids: list[int] = []
    found_names: list[str] = []
    not_found: list[str] = []
    external_emails: list[str] = []

    nick_results = await asyncio.gather(
        *(bitrix.find_user_by_nickname(nick) for nick in nicknames)
    )
    for nick, (uid, full_name) in zip(nicknames, nick_results):
        if uid:
            attendee_ids.append(uid)
            found_names.append(full_name or nick)
        else:
            not_found.append(f"@{nick}")

    invite_emails: list[str] = []
    email_results = await asyncio.gather(
        *(bitrix.resolve_email_user(email) for email in emails),
        return_exceptions=True,
    )
    for email, result in zip(emails, email_results):
        if isinstance(result, Exception):
            logger.error("Failed to find user by email %s: %s", email, result)
            invite_emails.append(email)
        else:
            uid, name = result
            if uid:
                attendee_ids.append(uid)
                external_emails.append(f"{name} ({email})" if name else email)
            else:
                invite_emails.append(email)

    title = context[:80] if context else "Встреча"
    description = context or ""
    if invite_emails:
        description += "\n\nПригласить по email: " + ", ".join(invite_emails)

    owner_user_id = db_user["bitrix_user_id"]
    result = await bitrix.create_meeting(
        title=title,
        date=dt,
        owner_user_id=owner_user_id,
        description=description,
        attendee_ids=attendee_ids if attendee_ids else None,
    )

    event_id = result.get("id", "?")
    bitrix_url = f"https://{settings.bitrix_domain}/company/personal/user/{owner_user_id}/calendar/?EVENT_ID={event_id}"

    reply_text = _build_meeting_reply(
        dt, event_id, bitrix_url,
        found_names=found_names,
        external_emails=external_emails,
        invite_emails=invite_emails,
        not_found=not_found,
        context=context,
    )
    await message.reply(reply_text, reply_markup=MENU_KB)


# ── Interactive search handlers (DM flow) ─────────────────────

@router.callback_query(F.data == "mtg:cancel", MeetingSetup.searching_attendee)
async def handle_mtg_cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Создание встречи отменено.", reply_markup=MENU_KB)
    await callback.answer()


@router.message(MeetingSetup.searching_attendee, F.text)
async def handle_mtg_search_input(message: Message, state: FSMContext, db_user: DbUser, bitrix):
    query = (message.text or "").strip()
    data = await state.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    add_me = db_user["bitrix_user_id"] not in attendee_ids

    if not query:
        await message.reply("Напиши имя или фамилию коллеги:", reply_markup=_cancel_kb(show_add_me=add_me))
        return

    users = await bitrix.search_users(query)
    if not users:
        await message.reply(
            "Никого не нашёл, попробуй другое имя:",
            reply_markup=_cancel_kb(show_add_me=add_me),
        )
        return

    await message.reply("Выбери коллегу:", reply_markup=_search_results_kb(users))


@router.callback_query(F.data.startswith("pick:"), MeetingSetup.searching_attendee)
async def handle_mtg_pick_user(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split(":", 2)
    if len(parts) < 3:
        await callback.answer("Ошибка данных кнопки", show_alert=True)
        return

    bitrix_id = int(parts[1])
    name = parts[2]

    data = await state.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await state.update_data(attendee_ids=attendee_ids, attendee_names=attendee_names)

    db_user = await db.get_user(callback.from_user.id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    selected = ", ".join(attendee_names)
    await callback.message.edit_text(
        f"Выбраны: {selected}",
        reply_markup=_search_status_kb(attendee_names, show_add_me=add_me),
    )
    await callback.answer()


@router.callback_query(F.data == "search:addme", MeetingSetup.searching_attendee)
async def handle_mtg_add_me(callback: CallbackQuery, state: FSMContext):
    db_user = await db.get_user(callback.from_user.id)
    bitrix_id = db_user["bitrix_user_id"]
    name = db_user["display_name"]

    data = await state.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    if bitrix_id not in attendee_ids:
        attendee_ids.append(bitrix_id)
        attendee_names.append(name)
        await state.update_data(attendee_ids=attendee_ids, attendee_names=attendee_names)

    selected = ", ".join(attendee_names)
    await callback.message.edit_text(
        f"Выбраны: {selected}",
        reply_markup=_search_status_kb(attendee_names, show_add_me=False),
    )
    await callback.answer()


@router.callback_query(F.data == "search:more", MeetingSetup.searching_attendee)
async def handle_mtg_add_more(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])
    db_user = await db.get_user(callback.from_user.id)
    add_me = db_user and db_user["bitrix_user_id"] not in attendee_ids
    await callback.message.edit_text("Напиши имя или фамилию коллеги:", reply_markup=_cancel_kb(show_add_me=add_me))
    await callback.answer()


@router.callback_query(F.data == "search:done", MeetingSetup.searching_attendee)
async def handle_mtg_done(callback: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    attendee_ids: list[int] = data.get("attendee_ids", [])

    if not attendee_ids:
        await callback.answer("Сначала выбери хотя бы одного участника", show_alert=True)
        return

    await state.set_state(MeetingSetup.waiting_for_title)
    await callback.message.edit_text("Напиши тему встречи:")
    await callback.answer()


@router.message(MeetingSetup.waiting_for_title, F.text)
async def handle_mtg_title_input(message: Message, state: FSMContext, bitrix):
    title = (message.text or "").strip()
    if not title:
        await message.reply("Напиши тему встречи текстом:")
        return

    data = await state.get_data()
    dt = datetime.fromisoformat(data["dt"])
    context = data.get("context", "")
    attendee_ids: list[int] = data.get("attendee_ids", [])
    attendee_names: list[str] = data.get("attendee_names", [])

    # Use user-provided title, keep context as description
    if context:
        context = f"{title}\n\n{context}"
    else:
        context = title

    db_user = await db.get_user(message.from_user.id)
    await _do_create_meeting(message, db_user, bitrix, dt, context, attendee_ids, attendee_names)
    await state.clear()
    logger.info("*** Meeting created via interactive search: %s attendees=%s", title, attendee_ids)
