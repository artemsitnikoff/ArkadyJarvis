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

CREATE TABLE IF NOT EXISTS muted_groups (
    chat_id INTEGER PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS recruiter_contacts (
    telegram_user_id INTEGER PRIMARY KEY,
    phone            TEXT NOT NULL,
    applicant_id     INTEGER NOT NULL,
    job_id           INTEGER NOT NULL,
    job_name         TEXT,
    applicant_name   TEXT,
    created_at       TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS zabbix_problems (
    problem_id    INTEGER PRIMARY KEY,
    host          TEXT,
    name          TEXT,
    severity      TEXT,
    opened_at     TEXT,
    resolved_at   TEXT,
    jira_task_key TEXT,
    raw_text      TEXT,
    last_seen     TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_zabbix_unresolved
    ON zabbix_problems(resolved_at, jira_task_key, opened_at);

-- DCJ — справочник проектов из корневого DCJ.xlsx
CREATE TABLE IF NOT EXISTS dcj_projects (
    project_key  TEXT PRIMARY KEY,
    name         TEXT,
    is_internal  INTEGER NOT NULL DEFAULT 0,    -- 1 если "Внутренний=Да"
    direction    TEXT,                          -- "WEB - ПиК" / "1С" / "Маркетинг" / ...
    category     TEXT,
    updated_at   TEXT DEFAULT (datetime('now'))
);

-- Менеджеры проектов и их разработчики (для Мисис Хадсон)
-- developer_pattern — substring (фамилия/часть) для поиска в Jira authorName или Bitrix LAST_NAME
CREATE TABLE IF NOT EXISTS hudson_managers (
    manager_name        TEXT NOT NULL,        -- ФИО как в Bitrix
    manager_bitrix_id   INTEGER,              -- resolved через user.get LAST_NAME
    developer_pattern   TEXT NOT NULL,        -- "Некрасова" / "Гусев" / etc.
    developer_bitrix_id INTEGER,              -- resolved при загрузке
    developer_email     TEXT,                 -- из Bitrix user.get
    PRIMARY KEY (manager_name, developer_pattern)
);
"""


MIGRATIONS: list[str] = [
    "DROP TABLE IF EXISTS jira_credentials;",
    "INSERT OR IGNORE INTO muted_groups (chat_id) VALUES (-1001408128567);",
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


async def get_user_by_bitrix_id(bitrix_user_id: int) -> DbUser | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM users WHERE bitrix_user_id = ?", (bitrix_user_id,)
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


# ── Muted groups ─────────────────────────────────────────────

async def is_group_muted(chat_id: int) -> bool:
    db = get_db()
    async with db.execute(
        "SELECT 1 FROM muted_groups WHERE chat_id = ?", (chat_id,)
    ) as cur:
        return await cur.fetchone() is not None


async def add_muted_group(chat_id: int) -> None:
    db = get_db()
    await db.execute(
        "INSERT OR IGNORE INTO muted_groups (chat_id) VALUES (?)", (chat_id,)
    )
    await db.commit()


async def remove_muted_group(chat_id: int) -> None:
    db = get_db()
    await db.execute("DELETE FROM muted_groups WHERE chat_id = ?", (chat_id,))
    await db.commit()


# ── Recruiter contacts ────────────────────────────────────────

async def save_recruiter_contact(
    telegram_user_id: int,
    phone: str,
    applicant_id: int,
    job_id: int,
    job_name: str,
    applicant_name: str,
) -> None:
    db = get_db()
    await db.execute(
        """INSERT INTO recruiter_contacts
               (telegram_user_id, phone, applicant_id, job_id, job_name, applicant_name)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(telegram_user_id) DO UPDATE SET
               phone = excluded.phone,
               applicant_id = excluded.applicant_id,
               job_id = excluded.job_id,
               job_name = excluded.job_name,
               applicant_name = excluded.applicant_name,
               created_at = datetime('now')""",
        (telegram_user_id, phone, applicant_id, job_id, job_name, applicant_name),
    )
    await db.commit()


async def get_recruiter_contact(telegram_user_id: int) -> dict | None:
    db = get_db()
    async with db.execute(
        "SELECT * FROM recruiter_contacts WHERE telegram_user_id = ?", (telegram_user_id,)
    ) as cur:
        row = await cur.fetchone()
        return dict(row) if row else None


# ── Zabbix problems ──────────────────────────────────────────

async def upsert_zabbix_problem(
    problem_id: int,
    host: str | None,
    name: str | None,
    severity: str | None,
    opened_at: str,
    raw_text: str,
) -> None:
    """Insert a new Zabbix problem; on conflict keep the earliest opened_at."""
    db = get_db()
    await db.execute(
        """INSERT INTO zabbix_problems
               (problem_id, host, name, severity, opened_at, raw_text, last_seen)
           VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(problem_id) DO UPDATE SET
               host = COALESCE(excluded.host, host),
               name = COALESCE(excluded.name, name),
               severity = COALESCE(excluded.severity, severity),
               raw_text = COALESCE(excluded.raw_text, raw_text),
               last_seen = datetime('now')""",
        (problem_id, host, name, severity, opened_at, raw_text),
    )
    await db.commit()


async def mark_zabbix_resolved(problem_id: int, resolved_at: str) -> None:
    """Idempotent: sets resolved_at if not already set. Inserts stub if no row exists."""
    db = get_db()
    await db.execute(
        """INSERT INTO zabbix_problems (problem_id, resolved_at, last_seen)
           VALUES (?, ?, datetime('now'))
           ON CONFLICT(problem_id) DO UPDATE SET
               resolved_at = COALESCE(zabbix_problems.resolved_at, excluded.resolved_at),
               last_seen = datetime('now')""",
        (problem_id, resolved_at),
    )
    await db.commit()


async def set_zabbix_jira_key(problem_id: int, jira_task_key: str) -> None:
    db = get_db()
    await db.execute(
        "UPDATE zabbix_problems SET jira_task_key = ? WHERE problem_id = ?",
        (jira_task_key, problem_id),
    )
    await db.commit()


async def get_unresolved_zabbix_problems(opened_before: str) -> list[dict]:
    """Problems still open (no resolved_at) without a Jira task, opened earlier than cutoff."""
    db = get_db()
    async with db.execute(
        """SELECT * FROM zabbix_problems
           WHERE resolved_at IS NULL
             AND jira_task_key IS NULL
             AND opened_at IS NOT NULL
             AND opened_at <= ?
           ORDER BY opened_at""",
        (opened_before,),
    ) as cur:
        return [dict(r) for r in await cur.fetchall()]
