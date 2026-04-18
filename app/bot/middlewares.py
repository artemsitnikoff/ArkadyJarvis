import logging
from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app import db

logger = logging.getLogger("arkadyjarvis")

# Slash commands that don't require auth. Everything else (menu buttons,
# /summary in groups, etc.) is either gated by handler-level access checks
# (GLAFIRA_ALLOWED, RECRUITER_ALLOWED) or relies on db_user being present.
PUBLIC_COMMANDS = {"/start", "/help"}

# /summary is the only text command that needs auth+is not a slash-menu button.
AUTH_COMMANDS = {"/summary"}


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


def _first_word(text: str) -> str:
    return text.split()[0].split("@")[0] if text else ""


class AuthMiddleware(BaseMiddleware):
    """Inject `db_user` into handler data and gate auth-required commands.

    Messages:
      - /start and /help pass through without auth;
      - In muted groups the bot stays silent for any slash command
        (and replies only to explicit /commands with a short mute notice);
      - /summary requires an authorized user.

    Callback queries:
      - always get `db_user` injected;
      - handlers enforce their own access rules (GLAFIRA_ALLOWED,
        RECRUITER_ALLOWED, etc.).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Callback queries — inject db_user and hand over to handlers.
        if isinstance(event, CallbackQuery):
            user = await db.get_user(event.from_user.id) if event.from_user else None
            data["db_user"] = user
            return await handler(event, data)

        if not isinstance(event, Message):
            return await handler(event, data)

        # ── Message handling ─────────────────────────────────────
        first = _first_word(event.text or "")

        # Public slash commands — always allow.
        if first in PUBLIC_COMMANDS:
            return await handler(event, data)

        # Muted groups — bot collects messages but refuses to respond to
        # slash commands with a short notice; everything else is buffered.
        if event.chat.type in ("group", "supergroup"):
            if await db.is_group_muted(event.chat.id):
                if first.startswith("/"):
                    await event.reply(
                        "Мне запретили отвечать в этой группе. "
                        "Группа внесена в список исключений."
                    )
                    return None
                return await handler(event, data)

        # Load user if exists.
        user = await db.get_user(event.from_user.id) if event.from_user else None
        data["db_user"] = user

        # Auth-required slash commands.
        if first in AUTH_COMMANDS and (not user or not user.get("bitrix_user_id")):
            if event.chat.type in ("group", "supergroup"):
                await event.reply("Сначала авторизуйся: напиши мне /start в личку")
            else:
                await event.answer("Сначала авторизуйся через /start")
            return None

        return await handler(event, data)
