#!/usr/bin/env python3
"""
One-time script to create a Telethon StringSession for the userbot.

Run: python scripts/create_userbot_session.py
Then copy TELETHON_SESSION=... into your .env file.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

API_ID = 33988209
API_HASH = "e88485f4f18cd2bee1f8552c655a9893"


async def main() -> None:
    print("Создаём сессию для userbot (@digitalclouds)...")
    client = TelegramClient(StringSession(), API_ID, API_HASH)
    await client.start()
    session_string = client.session.save()
    me = await client.get_me()
    print(f"\n✅ Авторизован как @{me.username} (id={me.id})")
    print("\n=== Добавь в .env ===")
    print(f"TELETHON_API_ID={API_ID}")
    print(f"TELETHON_API_HASH={API_HASH}")
    print(f"TELETHON_SESSION={session_string}")
    await client.disconnect()


asyncio.run(main())
