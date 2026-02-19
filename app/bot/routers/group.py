import logging

from aiogram import Router
from aiogram.types import ChatMemberUpdated
from aiogram.filters import ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER

from app import db

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type in ("group", "supergroup"):
        await db.upsert_group_chat(chat.id, chat.title)
        logger.info("Bot added to group: %s (%s)", chat.title, chat.id)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER >> IS_NOT_MEMBER))
async def on_bot_removed(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type in ("group", "supergroup"):
        await db.remove_group_chat(chat.id)
        logger.info("Bot removed from group: %s (%s)", chat.title, chat.id)
