#!/usr/bin/env python3
"""Разовый запуск Мисис Хадсон за прошлую полную неделю (пн-вс).

Запускает то же, что и cron-job Пн 11:00, но прямо сейчас.

    # Тёплый прогон — ничего не пошлёт и не создаст в Jira:
    python scripts/run_hudson_now.py --dry-run

    # Реальный прогон за прошлую неделю (Пн-Вс):
    python scripts/run_hudson_now.py

    # За позапрошлую неделю (offset 1):
    python scripts/run_hudson_now.py --offset 1

    # Произвольный диапазон:
    python scripts/run_hudson_now.py --since 2026-05-12 --until 2026-05-18
"""
import argparse
import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from app.bot.create import create_bot  # noqa: E402
from app.db import close_db, init_db  # noqa: E402
from app.services.hudson_analyzer import build_reports  # noqa: E402
from app.services.hudson_notifier import notify  # noqa: E402
from app.services.openrouter_client import OpenRouterClient  # noqa: E402


def _last_full_week(offset: int = 0) -> tuple[date, date]:
    """Понедельник-воскресенье прошлой недели (с учётом offset = N недель назад
    относительно «прошлой»)."""
    today = date.today()
    # текущий понедельник
    this_monday = today - timedelta(days=today.weekday())
    # прошлый понедельник
    last_monday = this_monday - timedelta(days=7 * (1 + offset))
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="не слать Telegram и не создавать Jira-задач")
    ap.add_argument("--offset", type=int, default=0, help="0 = прошлая неделя, 1 = позапрошлая и т.д.")
    ap.add_argument("--since", help="YYYY-MM-DD")
    ap.add_argument("--until", help="YYYY-MM-DD")
    args = ap.parse_args()

    if args.since and args.until:
        since = datetime.strptime(args.since, "%Y-%m-%d").date()
        until = datetime.strptime(args.until, "%Y-%m-%d").date()
    else:
        since, until = _last_full_week(args.offset)

    print(f"=== Hudson manual run: {since} → {until} (dry={args.dry_run}) ===\n")
    await init_db()
    openrouter = OpenRouterClient()
    bot = create_bot()
    try:
        reports = await build_reports(since, until, openrouter)
        if not reports:
            print("Нет данных")
            return

        # Краткий лог в консоль
        by_manager: dict[str, list] = {}
        for r in reports:
            by_manager.setdefault(r.manager_name, []).append(r)
        for mgr in sorted(by_manager):
            print(f"\n— {mgr} —")
            for r in sorted(by_manager[mgr], key=lambda x: x.name):
                flag = "🔴" if r.is_under_norm else "🟢"
                print(
                    f"  {flag} {r.name:<22} "
                    f"всего={r.total_hours:5.1f}h  "
                    f"внутр={r.internal_hours:5.1f}h  "
                    f"внешн={r.external_hours:5.1f}h  "
                    f"плохих={len(r.bad_comments)}/{len(r.entries)}"
                )

        print("\n=== Notifier ===")
        stats = await notify(reports, since, until, bot, dry_run=args.dry_run)
        print(f"  {stats}")
    finally:
        await bot.session.close()
        await openrouter.close()
        await close_db()


asyncio.run(main())
