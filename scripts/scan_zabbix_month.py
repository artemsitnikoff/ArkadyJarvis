#!/usr/bin/env python3
"""
One-shot script: scan ZABBIX_CHANNEL_ID history for the past 30 days via the
Telethon userbot, populate `zabbix_problems` table, then create Jira tasks
in the DA project for everything still unresolved.

Run:
    docker compose exec bot python scripts/scan_zabbix_month.py
"""
import asyncio
import logging
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
# Silence Telethon's internal chatter, keep our own logger
logging.getLogger("telethon").setLevel(logging.WARNING)

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import close_db, get_db, init_db  # noqa: E402
from app.services.jira_client import JiraClient  # noqa: E402
from app.services.zabbix_monitor import (  # noqa: E402
    ESCALATING_SEVERITIES,
    check_unresolved_and_create_jira,
    parse_zabbix_message,
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
    print(f"\n{'='*60}")
    print(f"Userbot: @{me.username} (id={me.id})")
    print(f"Channel: {settings.zabbix_channel_id}")

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    print(f"Cutoff:  {cutoff.isoformat()} (last 30 days)")
    print(f"{'='*60}\n")

    # Prime the entity cache: without this Telethon can't resolve channels
    # the userbot hasn't seen recently (PeerChannel without access_hash).
    print("Loading dialogs to cache channel entity…")
    try:
        async for _ in client.iter_dialogs(limit=200):
            pass
    except Exception as e:
        print(f"  warning: iter_dialogs failed: {e}")

    try:
        entity = await client.get_entity(settings.zabbix_channel_id)
        print(f"  → channel resolved: {getattr(entity, 'title', '?')}\n")
    except Exception as e:
        sys.exit(
            f"\n❌ Не могу прочитать канал {settings.zabbix_channel_id}.\n"
            f"   Userbot @{me.username} НЕ состоит в этом канале — "
            f"добавь его как подписчика и запусти скрипт ещё раз.\n\n"
            f"   Telethon error: {e}"
        )

    print("Fetching channel history…")
    messages = []
    async for msg in client.iter_messages(entity):
        if msg.date < cutoff:
            break
        if msg.text:
            messages.append(msg)
    print(f"  → fetched {len(messages)} text messages\n")

    # Process oldest-first so opens land before closes
    messages.sort(key=lambda m: m.date)

    opens = 0
    closes = 0
    skipped = 0
    severities: Counter = Counter()

    print("Parsing & saving to DB…")
    for msg in messages:
        alert = parse_zabbix_message(msg.text)
        if not alert:
            skipped += 1
            continue
        if alert.is_open:
            opens += 1
            severities[alert.severity or "?"] += 1
        else:
            closes += 1
        await process_alert_message(msg.text, msg.date)

    print(f"  → opens: {opens}")
    print(f"  → closes: {closes}")
    print(f"  → non-zabbix skipped: {skipped}")
    print(f"  → severity breakdown: {dict(severities)}\n")

    # Show what's still unresolved BEFORE escalation
    db = get_db()
    async with db.execute(
        "SELECT severity, COUNT(*) FROM zabbix_problems "
        "WHERE resolved_at IS NULL AND jira_task_key IS NULL "
        "GROUP BY severity"
    ) as cur:
        unresolved_by_sev = await cur.fetchall()

    print("Currently unresolved (no Jira task yet):")
    for sev, cnt in unresolved_by_sev:
        marker = "🔥" if sev in ESCALATING_SEVERITIES else "  "
        print(f"  {marker} {sev or '<none>'}: {cnt}")
    print()

    print(f"Creating Jira tasks in project {settings.zabbix_jira_project} "
          f"for unresolved >24h with severity Warning+…")
    async with JiraClient() as jira:
        created = await check_unresolved_and_create_jira(jira)

    # Show created tasks
    async with db.execute(
        "SELECT jira_task_key, host, severity, name FROM zabbix_problems "
        "WHERE jira_task_key IS NOT NULL AND last_seen >= datetime('now', '-1 hour') "
        "ORDER BY jira_task_key DESC LIMIT 50"
    ) as cur:
        recent = await cur.fetchall()

    if recent:
        print(f"\nJira tasks created in this run ({created}):")
        for key, host, sev, name in recent[:created]:
            print(f"  • {key}  [{sev}]  {host}  —  {(name or '')[:60]}")
    else:
        print(f"\nNo new Jira tasks created.")

    await client.disconnect()
    await close_db()
    print(f"\n{'='*60}\nDone.\n")


asyncio.run(main())
