import logging

from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    UserPrivacyRestrictedError,
)
from telethon.sessions import StringSession

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


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

    async def send_message(self, phone: str, text: str) -> bool:
        """Send message to user by phone number. Returns True on success."""
        phone = _normalize_phone(phone)
        if not phone:
            logger.warning("Userbot: empty phone after normalization")
            return False
        try:
            await self._client.send_message(phone, text)
            logger.info("Userbot: sent message to %s", phone)
            return True
        except (UserPrivacyRestrictedError, InputUserDeactivatedError) as e:
            logger.warning("Userbot: cannot message %s: %s", phone, e)
            return False
        except PeerFloodError:
            logger.error("Userbot: PeerFloodError — слишком много сообщений, Telegram заблокировал")
            raise
        except FloodWaitError as e:
            logger.warning("Userbot: FloodWait %ss for %s", e.seconds, phone)
            raise
        except Exception as e:
            logger.error("Userbot: unexpected error sending to %s: %s", phone, e, exc_info=True)
            return False
