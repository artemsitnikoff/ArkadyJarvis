import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TypedDict

import aiosqlite

from app.config import settings


class DbUser(TypedDict):
    telegram_id: int
    bitrix_user_id: int
    bitrix_domain: str | None
    display_name: str | None
    is_active: int
    created_at: str

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


MIGRATIONS: list[str] = [
    # Migration 1: initial schema is handled by SCHEMA constant above.
    # Future migrations go here as SQL strings, e.g.:
    # "ALTER TABLE users ADD COLUMN some_field TEXT DEFAULT '';",
]


async def _run_migrations(db: aiosqlite.Connection) -> None:
    """Run pending migrations based on schema_version table."""
    await db.execute(
        "CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)"
    )
    async with db.execute("SELECT version FROM schema_version") as cur:
        row = await cur.fetchone()
    current = row[0] if row else 0

    for i, sql in enumerate(MIGRATIONS, start=1):
        if i > current:
            logger.info("Running migration %d ...", i)
            await db.executescript(sql)
            if current == 0:
                await db.execute("INSERT INTO schema_version (version) VALUES (?)", (i,))
            else:
                await db.execute("UPDATE schema_version SET version = ?", (i,))
            current = i

    if not row and not MIGRATIONS:
        await db.execute("INSERT INTO schema_version (version) VALUES (0)")

    await db.commit()
    logger.info("Schema version: %d", current)


async def init_db() -> aiosqlite.Connection:
    global _db
    db_path = Path(settings.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _db = await aiosqlite.connect(str(db_path))
    _db.row_factory = aiosqlite.Row
    await _db.execute("PRAGMA journal_mode=WAL")
    await _db.execute("PRAGMA busy_timeout=5000")
    await _db.executescript(SCHEMA)
    await _run_migrations(_db)
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


async def get_user(telegram_id: int) -> DbUser | None:
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
    from app.crypto import encrypt

    db = get_db()
    encrypted_password = encrypt(jira_password)
    await db.execute(
        """INSERT INTO jira_credentials (telegram_id, jira_url, jira_username, jira_password)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(telegram_id) DO UPDATE SET
             jira_url      = excluded.jira_url,
             jira_username = excluded.jira_username,
             jira_password = excluded.jira_password""",
        (telegram_id, jira_url, jira_username, encrypted_password),
    )
    await db.commit()


async def get_jira_credentials(telegram_id: int) -> dict | None:
    from app.crypto import decrypt, encrypt

    db = get_db()
    async with db.execute(
        "SELECT * FROM jira_credentials WHERE telegram_id = ?", (telegram_id,)
    ) as cur:
        row = await cur.fetchone()
        if not row:
            return None

    creds = dict(row)
    try:
        creds["jira_password"] = decrypt(creds["jira_password"])
    except Exception:
        # Lazy migration: plaintext password from before encryption was added
        logger.info("Migrating plaintext Jira password for user %s", telegram_id)
        plaintext = creds["jira_password"]
        encrypted = encrypt(plaintext)
        await db.execute(
            "UPDATE jira_credentials SET jira_password = ? WHERE telegram_id = ?",
            (encrypted, telegram_id),
        )
        await db.commit()
    return creds


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
    # No commit here — WAL mode handles reads without it.
    # SQLite auto-commits on read or explicit flush.


async def get_buffered_messages(
    chat_id: int, since: datetime | None = None
) -> list[dict]:
    db = get_db()
    await db.commit()  # flush pending inserts before reading
    if since:
        sql = "SELECT * FROM message_buffer WHERE chat_id = ? AND sent_at >= ? ORDER BY sent_at"
        params = (chat_id, since.isoformat())
    else:
        sql = "SELECT * FROM message_buffer WHERE chat_id = ? ORDER BY sent_at"
        params = (chat_id,)
    async with db.execute(sql, params) as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def get_active_users() -> list[DbUser]:
    db = get_db()
    async with db.execute("SELECT * FROM users WHERE is_active = 1") as cur:
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


async def cleanup_old_messages(days: int = 7) -> int:
    db = get_db()
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    async with db.execute(
        "DELETE FROM message_buffer WHERE sent_at < ?", (cutoff,)
    ) as cur:
        count = cur.rowcount
    await db.commit()
    return count
