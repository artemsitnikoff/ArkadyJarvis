import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app import db

logger = logging.getLogger("arkadyjarvis")

PUBLIC_COMMANDS = {"/start", "/help"}

# Triggers that require Bitrix authorization
AUTH_TRIGGERS = [
    re.compile(r"(?i)^(сделай|создай)\s+встречу"),
    re.compile(r"(?i)^найди\s+время"),
    re.compile(r"(?i)^(сделай|создай)\s+задачу"),
    re.compile(r"(?i)^(сделай|создай)\s+лид"),
]


def _needs_auth(text: str) -> bool:
    """Check if this message is a command/trigger that requires Bitrix auth."""
    first_word = text.split()[0].split("@")[0] if text else ""
    if first_word in {"/summary", "/jira", "/skip"}:
        return True
    if text.lower().strip() == "суммаризация":
        return True
    return any(p.match(text) for p in AUTH_TRIGGERS)


class AuthMiddleware(BaseMiddleware):
    """Check authorization only for commands/triggers that need Bitrix.

    Group messages pass through freely (for buffering).
    Auto-replies (ситников) also pass through without auth.
    """

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        # Public commands — always allow
        if event.text and event.text.split()[0].split("@")[0] in PUBLIC_COMMANDS:
            return await handler(event, data)

        # Load user if exists
        user = await db.get_user(event.from_user.id) if event.from_user else None
        data["db_user"] = user

        # Check if this message needs auth
        text = event.text or ""
        if _needs_auth(text):
            if not user or not user.get("bitrix_user_id"):
                # /jira and /skip — allow (onboarding)
                first_cmd = text.split()[0].split("@")[0] if text else ""
                if first_cmd in {"/jira", "/skip"}:
                    return await handler(event, data)

                # In groups — short reply, in PM — full message
                if event.chat.type in ("group", "supergroup"):
                    await event.reply("Сначала авторизуйся: напиши мне /start в личку")
                else:
                    await event.answer("Сначала авторизуйся через /start")
                return None

        return await handler(event, data)
