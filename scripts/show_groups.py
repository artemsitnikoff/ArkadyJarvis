#!/usr/bin/env python3
"""Show all group chats in the database and their recent activity."""

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

    has_muted = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='muted_groups'"
    ).fetchone() is not None

    muted_ids: set[int] = set()
    if has_muted:
        muted_ids = {row[0] for row in conn.execute("SELECT chat_id FROM muted_groups")}

    groups = conn.execute("""
        SELECT
            g.chat_id,
            g.chat_title,
            g.summary_enabled,
            g.added_at,
            COUNT(m.id) AS msg_count_7d,
            MAX(m.sent_at) AS last_message_at
        FROM group_chats g
        LEFT JOIN message_buffer m ON m.chat_id = g.chat_id
        GROUP BY g.chat_id
        ORDER BY last_message_at DESC NULLS LAST
    """).fetchall()

    if not groups:
        print("No group chats in database.")
        return

    print(f"{'Title':<30} {'Chat ID':<16} {'Summary':<8} {'Muted':<6} {'Msgs 7d':<9} {'Added':<20} {'Last msg':<20}")
    print("-" * 112)

    for g in groups:
        title = (g["chat_title"] or "—")[:29]
        summary = "yes" if g["summary_enabled"] else "no"
        muted = "yes" if g["chat_id"] in muted_ids else "no"
        added = (g["added_at"] or "—")[:19]
        last_msg = (g["last_message_at"] or "—")[:19]

        print(
            f"{title:<30} {g['chat_id']:<16} {summary:<8} {muted:<6} "
            f"{g['msg_count_7d']:<9} {added:<20} {last_msg:<20}"
        )

    print(f"\nTotal: {len(groups)} groups")
    print("Note: 'Msgs 7d' is from message_buffer (cleaned up after 7 days)")

    conn.close()


if __name__ == "__main__":
    main()
