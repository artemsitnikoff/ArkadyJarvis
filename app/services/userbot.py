import logging
from collections.abc import Callable, Coroutine
from typing import Any

from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    UserPrivacyRestrictedError,
)
from telethon.sessions import StringSession
from telethon.tl.functions.contacts import DeleteContactsRequest, ImportContactsRequest
from telethon.tl.types import InputPhoneContact

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

ReplyHandler = Callable[[int, str], Coroutine[Any, Any, None]]


def _normalize_phone(phone: str) -> str:
    digits = "".join(c for c in phone if c.isdigit())
    if not digits:
        return ""
    if len(digits) == 11 and digits.startswith("8"):
        digits = "7" + digits[1:]
    return "+" + digits


class UserbotClient:
    def __init__(self):
        self._client = TelegramClient(
            StringSession(settings.telethon_session),
            settings.telethon_api_id,
            settings.telethon_api_hash,
        )
        self._reply_handler: ReplyHandler | None = None

        @self._client.on(events.NewMessage(incoming=True))
        async def _on_incoming(event: events.NewMessage.Event) -> None:
            text = event.raw_text or ""
            if self._reply_handler and text:
                try:
                    await self._reply_handler(event.sender_id, text)
                except Exception as e:
                    logger.error("Userbot: reply handler error: %s", e, exc_info=True)

    def set_reply_handler(self, handler: ReplyHandler) -> None:
        self._reply_handler = handler

    async def start(self) -> None:
        await self._client.connect()
        if not await self._client.is_user_authorized():
            raise RuntimeError(
                "Userbot: сессия недействительна. Запусти scripts/create_userbot_session.py заново."
            )
        me = await self._client.get_me()
        logger.info("Userbot connected as @%s (id=%s)", me.username, me.id)

    async def stop(self) -> None:
        await self._client.disconnect()

    async def resolve_phone(self, phone: str) -> int | None:
        """Import contact via MTProto to resolve phone → Telegram user_id. Returns None if not found."""
        phone = _normalize_phone(phone)
        if not phone:
            return None
        try:
            result = await self._client(ImportContactsRequest([
                InputPhoneContact(client_id=0, phone=phone, first_name="Candidate", last_name="")
            ]))
            if not result.users:
                logger.warning("Userbot: phone %s not found on Telegram", phone)
                return None
            return result.users[0].id
        except Exception as e:
            logger.error("Userbot: resolve_phone error for %s: %s", phone, e, exc_info=True)
            return None

    async def send_to_user(self, user_id: int, text: str) -> bool:
        """Send message to a known Telegram user_id. Returns True on success."""
        try:
            await self._client.send_message(user_id, text)
            logger.info("Userbot: sent to user_id=%s", user_id)
            return True
        except (UserPrivacyRestrictedError, InputUserDeactivatedError) as e:
            logger.warning("Userbot: cannot message user_id=%s: %s", user_id, e)
            return False
        except PeerFloodError:
            logger.error("Userbot: PeerFloodError — Telegram заблокировал за спам")
            raise
        except FloodWaitError as e:
            logger.warning("Userbot: FloodWait %ss", e.seconds)
            raise
        except Exception as e:
            logger.error("Userbot: send_to_user error user_id=%s: %s", user_id, e, exc_info=True)
            return False

    async def send_message(self, phone: str, text: str) -> bool:
        """Resolve phone and send message. Returns True on success."""
        user_id = await self.resolve_phone(phone)
        if not user_id:
            return False
        return await self.send_to_user(user_id, text)
