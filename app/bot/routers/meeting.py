import asyncio
import logging

from aiogram import F, Router
from aiogram.types import Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.db import DbUser
from app.utils import parse_attendees, parse_meeting_time

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(F.text.regexp(r"(?i)^(сделай|создай)\s+встречу"))
async def handle_create_meeting(message: Message, db_user: DbUser, bitrix):
    text = message.text or ""
    logger.info("*** TRIGGER: 'сделай встречу' in chat=%s from user=%s", message.chat.id, message.from_user.id)

    dt, err = parse_meeting_time(text)
    if err:
        await message.reply(err)
        return

    context = ""
    if message.reply_to_message and message.reply_to_message.text:
        context = message.reply_to_message.text

    nicknames, emails = parse_attendees(text)

    attendee_ids: list[int] = []
    found_names: list[str] = []
    not_found: list[str] = []
    external_emails: list[str] = []

    # Resolve nicknames in parallel
    nick_results = await asyncio.gather(
        *(bitrix.find_user_by_nickname(nick) for nick in nicknames)
    )
    for nick, (uid, full_name) in zip(nicknames, nick_results):
        if uid:
            attendee_ids.append(uid)
            found_names.append(full_name or nick)
        else:
            not_found.append(f"@{nick}")

    # Resolve emails in parallel
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

    reply_text = f"✅ Встреча создана: {dt:%d.%m.%Y} в {dt:%H:%M} (id: {event_id})\n🔗 {bitrix_url}"
    if found_names:
        reply_text += f"\n👥 Участники: {', '.join(found_names)}"
    if external_emails:
        reply_text += f"\n👥 По email: {', '.join(external_emails)}"
    if invite_emails:
        reply_text += f"\n📧 В описании (пригласить вручную): {', '.join(invite_emails)}"
    if not_found:
        reply_text += f"\n⚠️ Не найден: {', '.join(not_found)}"
    if context:
        reply_text += f"\n📝 {context}"
    await message.reply(reply_text, reply_markup=MENU_KB)
