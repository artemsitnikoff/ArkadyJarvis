"""Мисис Хадсон — кнопка в меню для показа текущего состояния worklog'ов.

Показывает сводную таблицу как у Алины (без рассылки и без Jira). Доступ
только TG IDs из HUDSON_ALLOWED (я + менеджеры P&Q).
"""
import html
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import CallbackQuery

from app.config import settings
from app.services.ai_client import AIClient

logger = logging.getLogger("arkadyjarvis")
router = Router()


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


HUDSON_ALLOWED = _parse_allowed_ids(settings.hudson_allowed)


async def enter_hudson(callback: CallbackQuery, ai_client: AIClient) -> None:
    """Показать сводку Хадсона за последние 7 дней (без рассылки)."""
    if callback.from_user.id not in HUDSON_ALLOWED:
        await callback.answer("🏠 Доступ закрыт", show_alert=True)
        return
    await callback.answer()
    wait = await callback.message.answer(
        "🏠 Хадсон собирает worklog'и WEB-ПиК за неделю, "
        "+ Haiku-классификация комментариев. Ждать минуту…",
    )

    from app.services.hudson_analyzer import build_reports
    from app.services.hudson_notifier import _format_alina_summary

    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=6)

    try:
        reports = await build_reports(since, until, ai_client)
    except Exception as e:
        logger.error("Hudson button: build_reports failed: %s", e, exc_info=True)
        await wait.edit_text(f"❌ Ошибка: {html.escape(str(e)[:300])}")
        return

    if not reports:
        await wait.edit_text("Нет данных по WEB-ПиК за период")
        return

    by_manager: dict[str, list] = {}
    for r in reports:
        by_manager.setdefault(r.manager_name, []).append(r)
    text = _format_alina_summary(by_manager, since, until)

    # Дополнительный блок: топ-5 плохих комментариев из всего отчёта
    all_bad: list[tuple[str, ...]] = []
    for r in reports:
        for entry, reason in r.bad_comments:
            all_bad.append((r.name, entry.issue_key, entry.hours, entry.comment, reason))
    if all_bad:
        all_bad.sort(key=lambda x: -x[2])  # по списанным часам
        bits = ["\n💬 <b>Топ плохих комментариев</b>"]
        for name, ikey, hours, comment, reason in all_bad[:5]:
            c = html.escape((comment or "(пусто)")[:50])
            r_short = html.escape(reason[:80])
            bits.append(
                f"• <b>{html.escape(name)}</b> · <code>{ikey}</code> "
                f"({hours:.1f}h): «{c}» — {r_short}"
            )
        text += "\n" + "\n".join(bits)

    if len(text) > 4000:
        # Telegram limit 4096 — режем
        text = text[:3900] + "\n\n…(обрезано)"
    await wait.edit_text(text)
