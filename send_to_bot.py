#!/usr/bin/env python3
"""Отправить сообщение ОТ СВОЕГО аккаунта (Telethon) — например, боту.

Сообщение приходит адресату как от твоего юзер-аккаунта (того, чья TELETHON_SESSION),
а не от бота. Сессию берём из переменной окружения, чтобы не хардкодить секрет.

    export TELETHON_SESSION='1ApWap...'        # та же строка, что в .env
    /tmp/tgvenv/bin/python send_to_bot.py @zeta963cy3c_bot "привет, это я"

Можно слать и по числовому id, и по @username. Боту в первый раз иногда нужно
сначала отправить "/start".
"""
import asyncio
import os
import sys

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 33988209
API_HASH = "e88485f4f18cd2bee1f8552c655a9893"


async def main() -> None:
    session = os.environ.get("TELETHON_SESSION")
    if not session:
        sys.exit("Нет TELETHON_SESSION в окружении: export TELETHON_SESSION='...'")
    if len(sys.argv) < 3:
        sys.exit('Использование: python send_to_bot.py <@username|id> "текст"')

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
