#!/usr/bin/env python3
"""Отправить сообщение ОТ СВОЕГО аккаунта (Telethon) — например, боту.

Сообщение приходит адресату как от твоего юзер-аккаунта (того, чья TELETHON_SESSION),
а не от бота.

Сессию берём (в порядке приоритета):
  1) из переменной окружения TELETHON_SESSION;
  2) из файла data/.telethon_session (он в .gitignore — в репозиторий НЕ попадёт).
Саму сессию в git НЕ коммитим: это полный доступ к аккаунту.

    # вариант с файлом (один раз вставить строку сессии):
    echo '1ApWap...' > data/.telethon_session
    /tmp/tgvenv/bin/python send_to_bot.py @zeta963cy3c_bot "привет, это я"

    # или через env:
    export TELETHON_SESSION='1ApWap...'
    /tmp/tgvenv/bin/python send_to_bot.py @zeta963cy3c_bot "привет"

Боту в первый раз иногда нужно сначала отправить "/start".
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 33988209
API_HASH = "e88485f4f18cd2bee1f8552c655a9893"

_SESSION_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", ".telethon_session",
)


def _load_session() -> str:
    """env TELETHON_SESSION → файл data/.telethon_session (gitignored)."""
    s = os.environ.get("TELETHON_SESSION")
    if s:
        return s.strip()
    if os.path.exists(_SESSION_FILE):
        with open(_SESSION_FILE, encoding="utf-8") as f:
            return f.read().strip()
    sys.exit(
        "Нет сессии. Либо: export TELETHON_SESSION='...'\n"
        f"Либо вставь строку сессии в {_SESSION_FILE} (он gitignored)."
    )


async def main() -> None:
    if len(sys.argv) < 3:
        sys.exit('Использование: python send_to_bot.py <@username|id> "текст"')
    session = _load_session()
    target = sys.argv[1]
    text = sys.argv[2]
    # числовой id → int, иначе @username/строка
    entity: int | str = int(target) if target.lstrip("-").isdigit() else target

    client = TelegramClient(StringSession(session), API_ID, API_HASH)
    await client.start()
    me = await client.get_me()
    msg = await client.send_message(entity, text)
    print(f"✅ Отправлено от @{me.username} (id={me.id}) → {target} (msg_id={msg.id})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
