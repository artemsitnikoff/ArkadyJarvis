import asyncio
import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.utils import DAY_NAMES_RU, merge_intervals, parse_attendees, parse_bitrix_dt

logger = logging.getLogger("arkadyjarvis")
router = Router()


# ── FSM states ────────────────────────────────────────────────

class BookSlot(StatesGroup):
    waiting_for_slot = State()
    waiting_for_topic = State()


# ── Helpers ───────────────────────────────────────────────────

def split_into_hourly_chunks(
    free_slots: list[tuple[datetime, datetime]],
) -> list[tuple[datetime, datetime]]:
    """Split free slots into ≤60-min chunks. Drop tail < 30 min."""
    chunks: list[tuple[datetime, datetime]] = []
    for start, end in free_slots:
        cursor = start
        while cursor < end:
            next_hour = cursor + timedelta(hours=1)
            chunk_end = min(next_hour, end)
            if (chunk_end - cursor) >= timedelta(minutes=30):
                chunks.append((cursor, chunk_end))
            cursor = next_hour
    return chunks


def build_slot_keyboard(
    days_with_chunks: list[tuple[date, list[tuple[datetime, datetime]]]],
) -> InlineKeyboardMarkup:
    """Build inline keyboard: day headers + slot buttons (up to 4 per row)."""
    rows: list[list[InlineKeyboardButton]] = []
    for day, chunks in days_with_chunks:
        if not chunks:
            continue
        day_label = f"📅 {DAY_NAMES_RU[day.weekday()]}, {day.strftime('%d.%m')}"
        rows.append([InlineKeyboardButton(text=day_label, callback_data=f"day:{day.strftime('%d%m')}")])
        row: list[InlineKeyboardButton] = []
        for s, e in chunks:
            label = f"{s.strftime('%H:%M')}–{e.strftime('%H:%M')}"
            cb = f"book:{day.strftime('%d%m')}:{s.strftime('%H%M')}:{e.strftime('%H%M')}"
            row.append(InlineKeyboardButton(text=label, callback_data=cb))
            if len(row) == 4:
                rows.append(row)
                row = []
        if row:
            rows.append(row)
    rows.append([InlineKeyboardButton(text="❌ Отмена", callback_data="book:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _compute_free_slots_for_day(
    day: date,
    user_ids: list[int],
    accessibility: dict,
) -> list[tuple[datetime, datetime]]:
    """Compute free slots for a single day given accessibility data."""
    day_start = datetime.combine(day, datetime.min.time().replace(hour=9))
    day_end = datetime.combine(day, datetime.min.time().replace(hour=19))

    busy_intervals: list[tuple[datetime, datetime]] = []
    for uid in user_ids:
        slots = accessibility.get(str(uid), [])
        for slot in slots:
            acc = slot.get("ACCESSIBILITY", "busy")
            if acc in ("free",):
                continue
            try:
                dt_from = parse_bitrix_dt(slot["DATE_FROM"])
                dt_to = parse_bitrix_dt(slot["DATE_TO"])
                offset_from = int(slot.get("~USER_OFFSET_FROM", 0))
                offset_to = int(slot.get("~USER_OFFSET_TO", 0))
                dt_from -= timedelta(seconds=offset_from)
                dt_to -= timedelta(seconds=offset_to)
            except Exception as e:
                logger.warning("Skip slot parse error: %s | %s", e, slot)
                continue
            if dt_to <= day_start or dt_from >= day_end:
                continue
            busy_intervals.append((max(dt_from, day_start), min(dt_to, day_end)))

    merged = merge_intervals(busy_intervals)

    free_slots: list[tuple[datetime, datetime]] = []
    cursor = day_start
    for b_start, b_end in merged:
        if cursor < b_start:
            free_slots.append((cursor, b_start))
        cursor = max(cursor, b_end)
    if cursor < day_end:
        free_slots.append((cursor, day_end))

    return [(s, e) for s, e in free_slots if (e - s) >= timedelta(minutes=30)]


# ── Handlers ──────────────────────────────────────────────────

@router.message(F.text.regexp(r"(?i)^найди\s+время"))
async def handle_find_time(message: Message, state: FSMContext, db_user: DbUser, bitrix):
    text = message.text or ""
    logger.info("*** TRIGGER: 'найди время' in chat=%s from user=%s", message.chat.id, message.from_user.id)

    nicknames, _ = parse_attendees(text)
    if not nicknames:
        await message.reply("Укажи участников: Найди время @nick1 @nick2")
        return

    user_ids: list[int] = []
    user_names: list[str] = []
    not_found: list[str] = []
    nick_results = await asyncio.gather(
        *(bitrix.find_user_by_nickname(nick) for nick in nicknames)
    )
    for nick, (uid, full_name) in zip(nicknames, nick_results):
        if uid:
            user_ids.append(uid)
            user_names.append(f"@{nick}")
        else:
            not_found.append(f"@{nick}")

    if not user_ids:
        msg = "❌ Никого не удалось найти в Bitrix"
        if not_found:
            msg += f"\n⚠️ Не найден: {', '.join(not_found)}"
        await message.reply(msg)
        return

    today = datetime.now(ZoneInfo(settings.timezone)).date()
    work_days: list[date] = []
    d = today
    while len(work_days) < 5:
        if d.weekday() < 5:
            work_days.append(d)
        d += timedelta(days=1)

    date_from = work_days[0].strftime("%Y-%m-%d")
    date_to = work_days[-1].strftime("%Y-%m-%d")

    accessibility = await bitrix.get_users_accessibility(user_ids, date_from, date_to)

    # Build text message + collect chunks for keyboard
    lines: list[str] = []
    lines.append(f"📅 Свободные слоты для {', '.join(user_names)}:")
    if not_found:
        lines.append(f"⚠️ Не найден: {', '.join(not_found)}")
    lines.append("")

    days_with_chunks: list[tuple[date, list[tuple[datetime, datetime]]]] = []

    for day in work_days:
        free_slots = _compute_free_slots_for_day(day, user_ids, accessibility)
        chunks = split_into_hourly_chunks(free_slots)
        days_with_chunks.append((day, chunks))

    has_any_chunks = any(chunks for _, chunks in days_with_chunks)
    if not has_any_chunks:
        lines.append("Свободных слотов не найдено")
        await message.reply("\n".join(lines).rstrip())
    else:
        keyboard = build_slot_keyboard(days_with_chunks)
        header = "\n".join(lines).rstrip()
        await message.reply(
            f"{header}\n\nНажми на слот — создам встречу:",
            reply_markup=keyboard,
        )
        await state.update_data(
            attendee_ids=user_ids,
            attendee_names=user_names,
            year=work_days[0].year,
        )
        await state.set_state(BookSlot.waiting_for_slot)

    logger.info("*** SENT free slots for %s", user_names)


@router.callback_query(F.data.startswith("day:"))
async def handle_day_header(callback: CallbackQuery):
    await callback.answer()


@router.callback_query(F.data == "book:cancel", BookSlot.waiting_for_slot)
async def handle_cancel_booking(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.edit_text("Поиск слотов отменён.", reply_markup=MENU_KB)
    await callback.answer()


@router.callback_query(F.data.startswith("book:"), BookSlot.waiting_for_slot)
async def handle_slot_selected(callback: CallbackQuery, state: FSMContext):
    # Parse callback_data: book:DDMM:HHMM:HHMM
    parts = callback.data.split(":")
    if len(parts) != 4:
        await callback.answer("Ошибка данных кнопки", show_alert=True)
        return

    _, day_str, start_str, end_str = parts
    data = await state.get_data()
    year = data.get("year", datetime.now(ZoneInfo(settings.timezone)).year)

    day = int(day_str[:2])
    month = int(day_str[2:])
    sh, sm = int(start_str[:2]), int(start_str[2:])
    eh, em = int(end_str[:2]), int(end_str[2:])

    slot_start = datetime(year, month, day, sh, sm)
    slot_end = datetime(year, month, day, eh, em)
    duration = int((slot_end - slot_start).total_seconds() // 60)

    label = f"{day_str[:2]}.{day_str[2:]} {start_str[:2]}:{start_str[2:]}–{end_str[:2]}:{end_str[2:]}"

    await state.update_data(
        slot_start=slot_start.isoformat(),
        slot_duration=duration,
        slot_label=label,
    )
    await state.set_state(BookSlot.waiting_for_topic)

    await callback.message.edit_text(f"Выбрано: {label}. Напиши тему встречи:")
    await callback.answer()


@router.message(BookSlot.waiting_for_topic)
async def handle_topic_input(message: Message, state: FSMContext, db_user: DbUser, bitrix):
    topic = (message.text or "").strip()
    if not topic:
        await message.reply("Напиши тему встречи текстом")
        return

    data = await state.get_data()
    slot_start = datetime.fromisoformat(data["slot_start"])
    duration = data["slot_duration"]
    attendee_ids = data["attendee_ids"]
    attendee_names = data.get("attendee_names", [])
    label = data.get("slot_label", "")

    owner_user_id = db_user["bitrix_user_id"]

    result = await bitrix.create_meeting(
        title=topic,
        date=slot_start,
        owner_user_id=owner_user_id,
        duration_minutes=duration,
        attendee_ids=attendee_ids,
    )

    event_id = result.get("id", "?")
    bitrix_url = f"https://{settings.bitrix_domain}/company/personal/user/{owner_user_id}/calendar/?EVENT_ID={event_id}"

    reply = f"✅ Встреча «{topic}» создана: {label}\n🔗 {bitrix_url}"
    if attendee_names:
        reply += f"\n👥 Участники: {', '.join(attendee_names)}"
    await message.reply(reply, reply_markup=MENU_KB)
    await state.clear()
    logger.info("*** Meeting booked from free slots: %s %s attendees=%s", topic, label, attendee_ids)


@router.callback_query(F.data.startswith("book:"))
async def handle_stale_slot(callback: CallbackQuery):
    await callback.answer("Кнопки устарели, запусти поиск заново", show_alert=True)
