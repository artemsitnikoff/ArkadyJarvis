#!/usr/bin/env python3
"""Dry-run для Мисис Хадсон: собирает worklog'и за последние N дней (default 7)
и печатает per-dev отчёт.

    python scripts/test_hudson.py             # 7 дней + AI + только аналитика
    python scripts/test_hudson.py 14          # 14 дней
    python scripts/test_hudson.py 7 --no-ai   # без классификации комментариев
    python scripts/test_hudson.py 7 --pipe    # ещё и пропишет dry-run уведомления
                                              # (что бы отправилось, без реальных send/Jira)
"""
import asyncio
import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from app.db import close_db, init_db  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.hudson_analyzer import (  # noqa: E402
    WEEKLY_HOURS_NORM,
    build_reports,
)


async def main() -> None:
    days = 7
    no_ai = False
    pipe = False
    for arg in sys.argv[1:]:
        if arg == "--no-ai":
            no_ai = True
        elif arg == "--pipe":
            pipe = True
        else:
            try:
                days = int(arg)
            except ValueError:
                pass

    until = date.today()
    since = until - timedelta(days=days - 1)
    print(f"\n=== Hudson dry-run: {since} → {until} ({days}d) ===")
    print(f"   (норма {WEEKLY_HOURS_NORM:.0f}h/нед, ai={'off' if no_ai else 'on'})\n")

    await init_db()
    ai = AIClient()
    try:
        reports = await build_reports(
            since, until, ai, skip_comment_classification=no_ai,
        )
        if not reports:
            print("Нет данных")
            return

        by_manager: dict[str, list] = {}
        for r in reports:
            by_manager.setdefault(r.manager_name, []).append(r)

        for mgr in sorted(by_manager):
            print(f"\n— Менеджер {mgr} —")
            for r in sorted(by_manager[mgr], key=lambda x: x.name):
                flag = "🔴" if r.is_under_norm else "🟢"
                bad_str = (
                    f"{len(r.bad_comments)}/{len(r.entries)}"
                    if not no_ai else f"—/{len(r.entries)}"
                )
                print(
                    f"  {flag} {r.name:<22} "
                    f"всего={r.total_hours:5.1f}h  "
                    f"внутр={r.internal_hours:5.1f}h  "
                    f"внешн={r.external_hours:5.1f}h  "
                    f"плохие={bad_str}"
                )
                for entry, reason in r.bad_comments[:3]:
                    c = (entry.comment or "(пусто)")[:50]
                    print(
                        f"       ⚠️  {entry.issue_key} ({entry.hours:.1f}h): "
                        f"«{c}» — {reason[:80]}"
                    )
        if pipe:
            print("\n=== Pipe dry-run (что бы отправилось / в Jira) ===\n")
            from app.services.hudson_notifier import notify
            stats = await notify(reports, since, until, bot=None, dry_run=True)
            print(f"\n  итог: {stats}")
    finally:
        await close_db()


asyncio.run(main())
