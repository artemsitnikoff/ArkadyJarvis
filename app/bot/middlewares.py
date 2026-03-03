import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import Message

from app import db
from app.db import DbUser

logger = logging.getLogger("arkadyjarvis")


class ErrorMiddleware(BaseMiddleware):
    """Catch unhandled exceptions in handlers, log them, reply with generic error."""

    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            logger.error(
                "Unhandled error in chat=%s user=%s",
                event.chat.id,
                event.from_user.id if event.from_user else "?",
                exc_info=True,
            )
            try:
                await event.reply("❌ Произошла ошибка. Попробуй ещё раз.")
            except Exception:
                pass
            return None


PUBLIC_COMMANDS = {"/start", "/help"}

# Triggers that require authorization
AUTH_TRIGGERS = [
    re.compile(r"(?i)^(сделай|создай)\s+встречу"),
    re.compile(r"(?i)^найди\s+время"),
    re.compile(r"(?i)^(сделай|создай)\s+задачу"),
    re.compile(r"(?i)^(сделай|создай)\s+лид"),
    re.compile(r"(?i)^(нарисуй|сгенерируй|картинк)"),
    re.compile(r"(?i)^(спроси\s+ai|вопрос)\s"),
]


def _needs_auth(text: str) -> bool:
    """Check if this message is a command/trigger that requires auth."""
    first_word = text.split()[0].split("@")[0] if text else ""
    if first_word in {"/summary"}:
        return True
    if text.lower().strip() == "суммаризация":
        return True
    return any(p.match(text) for p in AUTH_TRIGGERS)


def _would_trigger_response(text: str) -> bool:
    """Check if this message would trigger any bot response."""
    if not text:
        return False
    stripped = text.strip()
    # Any /command
    if stripped.startswith("/"):
        return True
    lower = stripped.lower()
    # суммаризация trigger
    if lower == "суммаризация":
        return True
    # ситников (auto_reply) — substring match
    if "ситников" in lower:
        return True
    # AUTH_TRIGGERS (meetings, tasks, leads, images, AI)
    return any(p.match(stripped) for p in AUTH_TRIGGERS)


class AuthMiddleware(BaseMiddleware):
    """Check authorization for all bot commands and triggers.

    Only /start and /help are public. Group messages without triggers
    pass through freely (for buffering). Auto-replies (ситников) also
    pass through without auth.
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

        # Muted groups — bot collects messages but doesn't respond to triggers
        if event.chat.type in ("group", "supergroup"):
            if await db.is_group_muted(event.chat.id):
                text = event.text or event.caption or ""
                if _would_trigger_response(text):
                    await event.reply(
                        "Мне запретили отвечать в этой группе. "
                        "Группа внесена в список исключений."
                    )
                    return None
                return await handler(event, data)

        # Load user if exists
        user = await db.get_user(event.from_user.id) if event.from_user else None
        data["db_user"] = user

        # Check if this message needs auth (text or photo caption)
        text = event.text or event.caption or ""
        if _needs_auth(text):
            if not user or not user.get("bitrix_user_id"):
                # In groups — short reply, in PM — full message
                if event.chat.type in ("group", "supergroup"):
                    await event.reply("Сначала авторизуйся: напиши мне /start в личку")
                else:
                    await event.answer("Сначала авторизуйся через /start")
                return None

        return await handler(event, data)
