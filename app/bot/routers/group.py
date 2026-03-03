import logging

from aiogram import F, Router
from aiogram.filters import BaseFilter, ChatMemberUpdatedFilter, IS_NOT_MEMBER, IS_MEMBER
from aiogram.types import ChatMemberUpdated, Message

from app import db
from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()


class BotMentionFilter(BaseFilter):
    """Match only when the bot's @username appears in message entities."""

    async def __call__(self, message: Message) -> bool:
        if not message.entities:
            return False
        bot_me = await message.bot.me()
        bot_username = bot_me.username.lower()
        return any(
            entity.type == "mention"
            and message.text[entity.offset + 1:entity.offset + entity.length].lower() == bot_username
            for entity in message.entities
        )


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_NOT_MEMBER >> IS_MEMBER))
async def on_bot_added(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type in ("group", "supergroup"):
        await db.upsert_group_chat(chat.id, chat.title)
        logger.info("Bot added to group: %s (%s)", chat.title, chat.id)


@router.message(BotMentionFilter(), F.chat.type.in_({"group", "supergroup"}))
async def handle_bot_mention(message: Message):
    """Reply with menu when bot is @mentioned in a group chat."""
    if await db.is_group_muted(message.chat.id):
        return
    await message.reply("Выбери команду — покажу подсказку:", reply_markup=MENU_KB)


@router.my_chat_member(ChatMemberUpdatedFilter(member_status_changed=IS_MEMBER >> IS_NOT_MEMBER))
async def on_bot_removed(event: ChatMemberUpdated):
    chat = event.chat
    if chat.type in ("group", "supergroup"):
        await db.remove_group_chat(chat.id)
        logger.info("Bot removed from group: %s (%s)", chat.title, chat.id)
