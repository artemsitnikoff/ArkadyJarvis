import logging
import random
import time
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
from app.utils import strip_numbered_item

logger = logging.getLogger("arkadyjarvis")

ReplyHandler = Callable[[int, str], Coroutine[Any, Any, None]]

# Пасхалка «ситников»: личный аккаунт салютует «Аве, Цезарь!» + цитата Сенеки.
# Кулдаун на чат — чтобы личный аккаунт не улетел во флуд/бан Telegram.
SENECA_COOLDOWN_SECONDS = 60

SENECA_PROMPT = """\
Напиши 3 цитаты Сенеки (Луций Анней Сенека, стоик). \
Бери РАЗНЫЕ произведения: "Нравственные письма к Луцилию", "О краткости жизни", \
"О блаженной жизни", "О гневе", "О стойкости мудреца", "О провидении" и др. \
Цитаты должны быть глубокие, философские, про жизнь, время, смерть, мужество, судьбу. \
НЕ повторяй самые заезженные ("Пока мы откладываем жизнь..." и т.п.). \
Каждая цитата — 1-2 предложения. Формат:
1. ...
2. ...
3. ...
Без вступления, без указания источника, только сами цитаты. Каждый раз НОВЫЕ."""


def _pick_one(text: str) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return strip_numbered_item(random.choice(lines))


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
        self._ai_client: Any = None  # для пасхалки «ситников» → Аве, Цезарь + Сенека
        self._seneca_cooldown: dict[int, float] = {}

        @self._client.on(events.NewMessage(incoming=True))
        async def _on_incoming(event: events.NewMessage.Event) -> None:
            text = event.raw_text or ""
            if self._reply_handler and text:
                try:
                    await self._reply_handler(event.sender_id, text)
                except Exception as e:
                    logger.error("Userbot: reply handler error: %s", e, exc_info=True)
            if self._ai_client and "ситников" in text.lower():
                try:
                    await self._maybe_seneca(event)
                except Exception as e:
                    logger.error("Userbot: seneca error: %s", e, exc_info=True)

    def set_reply_handler(self, handler: ReplyHandler) -> None:
        self._reply_handler = handler

    def enable_seneca(self, ai_client: Any) -> None:
        """Включить пасхалку: на «ситников» во входящих сообщениях личный аккаунт
        отвечает «Аве, Цезарь!» + случайной цитатой Сенеки (Haiku)."""
        self._ai_client = ai_client

    async def _maybe_seneca(self, event: events.NewMessage.Event) -> None:
        """Ответить «Аве, Цезарь!» + цитата Сенеки. Кулдаун на чат против флуда."""
        chat_id = event.chat_id
        now = time.monotonic()
        if now - self._seneca_cooldown.get(chat_id, 0.0) < SENECA_COOLDOWN_SECONDS:
            return
        self._seneca_cooldown[chat_id] = now
        logger.info("Userbot: seneca trigger 'ситников' in chat=%s", chat_id)
        # Сразу — салют Цезарю (от лица пользователя).
        await event.reply("Аве, Цезарь!")
        # Затем — цитата Сенеки (генерация может занять пару секунд).
        text = await self._ai_client.complete(
            SENECA_PROMPT, model="claude-haiku-4-5-20251001",
        )
        quote = _pick_one(text)
        if quote:
            await event.respond(quote)

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
