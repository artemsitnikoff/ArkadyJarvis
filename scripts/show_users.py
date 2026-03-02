#!/usr/bin/env python3
"""Show all users in the database and their last activity."""

import sqlite3
import sys
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "arkadyjarvis.db"


def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_DB)
    if not Path(db_path).exists():
        print(f"DB not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    users = conn.execute("""
        SELECT
            u.telegram_id,
            u.display_name,
            u.bitrix_user_id,
            u.is_active,
            u.created_at,
            MAX(m.sent_at) AS last_message_at
        FROM users u
        LEFT JOIN message_buffer m ON m.sender_id = u.telegram_id
        GROUP BY u.telegram_id
        ORDER BY last_message_at DESC NULLS LAST
    """).fetchall()

    if not users:
        print("No users in database.")
        return

    print(f"{'Name':<20} {'TG ID':<14} {'Active':<8} {'Registered':<20} {'Last msg':<20}")
    print("-" * 84)

    for u in users:
        name = u["display_name"] or "—"
        active = "yes" if u["is_active"] else "no"
        registered = (u["created_at"] or "—")[:19]
        last_msg = (u["last_message_at"] or "—")[:19]

        print(f"{name:<20} {u['telegram_id']:<14} {active:<8} {registered:<20} {last_msg:<20}")

    print(f"\nTotal: {len(users)} users")
    print("Note: 'Last msg' is from message_buffer (cleaned up after 7 days)")

    conn.close()


if __name__ == "__main__":
    main()
