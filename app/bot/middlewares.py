import logging
import re
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import db

logger = logging.getLogger("arkadyjarvis")


class ErrorMiddleware(BaseMiddleware):
    """Catch unhandled exceptions in handlers (both messages and callbacks),
    log them and reply with a generic error message to the user."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception:
            chat_id: Any = "?"
            user_id: Any = "?"
            if isinstance(event, Message):
                chat_id = event.chat.id
                user_id = event.from_user.id if event.from_user else "?"
            elif isinstance(event, CallbackQuery):
                chat_id = event.message.chat.id if event.message else "?"
                user_id = event.from_user.id if event.from_user else "?"

            logger.error(
                "Unhandled error in chat=%s user=%s", chat_id, user_id, exc_info=True,
            )

            try:
                if isinstance(event, Message):
                    await event.reply("❌ Произошла ошибка. Попробуй ещё раз.")
                elif isinstance(event, CallbackQuery):
                    await event.answer("❌ Произошла ошибка", show_alert=True)
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
    """Inject `db_user` into handler data for both messages and callbacks,
    and gate auth-required message triggers.

    Only /start and /help are public. Group messages without triggers
    pass through freely (for buffering). Callback queries always get
    `db_user` injected — individual callbacks enforce their own access
    rules (e.g. GLAFIRA_ALLOWED, RECRUITER_ALLOWED).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Callback queries — inject db_user for handlers, no auth gate
        # (handlers themselves decide whether auth is needed).
        if isinstance(event, CallbackQuery):
            user = await db.get_user(event.from_user.id) if event.from_user else None
            data["db_user"] = user
            return await handler(event, data)

        if not isinstance(event, Message):
            return await handler(event, data)

        # ── Message handling ─────────────────────────────────────
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
