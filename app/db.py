import logging
from datetime import datetime, timedelta
from pathlib import Path

import aiosqlite

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

_db: aiosqlite.Connection | None = None

SCHEMA = """\
CREATE TABLE IF NOT EXISTS users (
    telegram_id    INTEGER PRIMARY KEY,
    bitrix_user_id INTEGER,
    bitrix_domain  TEXT,
    display_name   TEXT,
    is_active      INTEGER DEFAULT 1,
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS jira_credentials (
    telegram_id   INTEGER PRIMARY KEY REFERENCES users(telegram_id),
    jira_url      TEXT NOT NULL,
    jira_username TEXT NOT NULL,
    jira_password TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS group_chats (
    chat_id         INTEGER PRIMARY KEY,
    chat_title      TEXT,
    added_at        TEXT DEFAULT (datetime('now')),
    summary_enabled INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS message_buffer (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     INTEGER NOT NULL,
    sender_id   INTEGER NOT NULL,
    sender_name TEXT DEFAULT '',
    text        TEXT NOT NULL,
    sent_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_buffer_chat_date ON message_buffer(chat_id, sent_at);
"""


async def init_db() -> aiosqlite.Connection:
    global _db
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.executescript(SCHEMA)
    await _db.commit()
    logger.info("Database initialized: %s", db_path)
    return _db


async def close_db():
    global _db
    if _db:
        await _db.close()
        _db = None


def get_db() -> aiosqlite.Connection:
    assert _db is not None, "Database not initialized — call init_db() first"
    return _db


# ── Users ──────────────────────────────────────────────────────

async def upsert_user(
    telegram_id: int,
    bitrix_user_id: int | None = None,
    bitrix_domain: str | None = None,
    display_name: str | None = None,
) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO users (telegram_id, bitrix_user_id, bitrix_domain, display_name)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
             bitrix_user_id = COALESCE(excluded.bitrix_user_id, bitrix_user_id),
             bitrix_domain  = COALESCE(excluded.bitrix_domain, bitrix_domain),
             display_name   = COALESCE(excluded.display_name, display_name)""",
        (telegram_id, bitrix_user_id, bitrix_domain, display_name),
    )
    await db.commit()


async def get_user(telegram_id: int) -> dict | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Jira credentials ──────────────────────────────────────────

async def save_jira_credentials(
    telegram_id: int, jira_url: str, jira_username: str, jira_password: str
) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO jira_credentials (telegram_id, jira_url, jira_username, jira_password)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
             jira_url      = excluded.jira_url,
             jira_username = excluded.jira_username,
             jira_password = excluded.jira_password""",
        (telegram_id, jira_url, jira_username, jira_password),
    )
    await db.commit()


async def get_jira_credentials(telegram_id: int) -> dict | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM jira_credentials WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Group chats ───────────────────────────────────────────────

async def upsert_group_chat(chat_id: int, chat_title: str | None = None) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO group_chats (chat_id, chat_title)
           VALUES (?, ?)
           ON CONFLICT(chat_id) DO UPDATE SET chat_title = COALESCE(excluded.chat_title, chat_title)""",
        (chat_id, chat_title),
    )
    await db.commit()


async def remove_group_chat(chat_id: int) -> None:
    db = get_db()
    await db.execute("DELETE FROM group_chats WHERE chat_id = ?", (chat_id,))
    await db.commit()


async def get_all_group_chats() -> list[dict]:
    db = get_db()
    async with db.execute(
        "SELECT * FROM group_chats WHERE summary_enabled = 1"
    ) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ── Message buffer ────────────────────────────────────────────

async def buffer_message(
    chat_id: int, sender_id: int, sender_name: str, text: str, sent_at: datetime
) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO message_buffer (chat_id, sender_id, sender_name, text, sent_at)
           VALUES (?, ?, ?, ?, ?)""",
        (chat_id, sender_id, sender_name, text, sent_at.isoformat()),
    )
    await db.commit()


async def get_buffered_messages(
    chat_id: int, since: datetime | None = None
) -> list[dict]:
    db = get_db()
    if since:
        sql = "SELECT * FROM message_buffer WHERE chat_id = ? AND sent_at >= ? ORDER BY sent_at"
        params = (chat_id, since.isoformat())
    else:
        sql = "SELECT * FROM message_buffer WHERE chat_id = ? ORDER BY sent_at"
        params = (chat_id,)
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def cleanup_old_messages(days: int = 7) -> int:
    db = get_db()
    cutoff = (datetime.utcnow() - timedelta(days=days)).isoformat()
    async with db.execute(
        "DELETE FROM message_buffer WHERE sent_at < ?", (cutoff,)
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count
