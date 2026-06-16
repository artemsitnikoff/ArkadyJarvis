#!/usr/bin/env python3
"""Сгенерировать TELETHON_SESSION для userbot.

Запусти на машине, где можешь ПОЛУЧИТЬ код входа на аккаунт userbot
(там, где этот аккаунт залогинен в Telegram, или куда придёт СМС):

    pip install telethon
    python gen_session.py

Введи телефон (+7...), код из Telegram, при 2FA — пароль.
В конце скопируй строку TELETHON_SESSION=... и вставь в .env на сервере,
затем на сервере: docker compose up -d --force-recreate
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 33988209
API_HASH = "e88485f4f18cd2bee1f8552c655a9893"


async def main() -> None:
    print("Создаём сессию для userbot…")
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start()  # спросит телефон, код, при 2FA — пароль
    me = await client.get_me()
    print(f"\n✅ Авторизован как @{me.username} (id={me.id})")
    print("\n=== Вставь эту строку в .env на сервере ===")
    print(f"TELETHON_SESSION={client.session.save()}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
