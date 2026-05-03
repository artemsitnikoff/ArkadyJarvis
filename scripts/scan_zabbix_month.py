#!/usr/bin/env python3
"""
One-shot script: scan ZABBIX_CHANNEL_ID history for the past 30 days via the
Telethon userbot, populate `zabbix_problems` table, then create Jira tasks
in the DA project for everything still unresolved.

Run:
    docker compose exec bot python scripts/scan_zabbix_month.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import close_db, init_db  # noqa: E402
from app.services.jira_client import JiraClient  # noqa: E402
from app.services.zabbix_monitor import (  # noqa: E402
    check_unresolved_and_create_jira,
    process_alert_message,
)


async def main() -> None:
    if not settings.zabbix_channel_id:
        sys.exit("ZABBIX_CHANNEL_ID is not configured in .env")
    if not settings.telethon_session:
        sys.exit("TELETHON_SESSION is not configured — run create_userbot_session.py first")

    await init_db()

    client = TelegramClient(
        StringSession(settings.telethon_session),
        settings.telethon_api_id,
        settings.telethon_api_hash,
    )
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("Userbot session expired — re-run create_userbot_session.py")
    me = await client.get_me()
    print(f"Userbot: @{me.username} (id={me.id})")

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    print(f"Fetching messages from channel {settings.zabbix_channel_id} since {cutoff.isoformat()}...")

    messages = []
    async for msg in client.iter_messages(settings.zabbix_channel_id):
        if msg.date < cutoff:
            break
        if msg.text:
            messages.append(msg)

    # Process oldest-first so opens land before closes
    messages.sort(key=lambda m: m.date)
    print(f"Fetched {len(messages)} messages — processing chronologically...")

    parsed = 0
    for msg in messages:
        if await process_alert_message(msg.text, msg.date):
            parsed += 1
    print(f"Parsed {parsed} Zabbix alert messages (other messages skipped)")

    print("\nCreating Jira tasks for unresolved problems...")
    async with JiraClient() as jira:
        created = await check_unresolved_and_create_jira(jira)
    print(f"Jira tasks created: {created}")

    await client.disconnect()
    await close_db()
    print("Done.")


asyncio.run(main())
