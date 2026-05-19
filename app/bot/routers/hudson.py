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
from app.services.openrouter_client import OpenRouterClient

logger = logging.getLogger("arkadyjarvis")
router = Router()


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


HUDSON_ALLOWED = _parse_allowed_ids(settings.hudson_allowed)


async def enter_hudson(
    callback: CallbackQuery, openrouter: OpenRouterClient, bitrix=None,
) -> None:
    """Показать сводку Хадсона за последние 7 дней (без рассылки).
    Haiku-классификатор через OpenRouter, чтобы не жечь Claude subscription."""
    if callback.from_user.id not in HUDSON_ALLOWED:
        await callback.answer("🏠 Доступ закрыт", show_alert=True)
        return
    await callback.answer()
    wait = await callback.message.answer(
        "🏠 Хадсон собирает worklog'и WEB-ПиК за неделю, "
        "+ Haiku-классификация комментариев. Ждать минуту…",
    )

    from app.services.hudson_analyzer import build_reports
    from app.services.hudson_notifier import _format_alina_messages

    until = date.today() - timedelta(days=1)
    since = until - timedelta(days=6)

    try:
        reports = await build_reports(since, until, openrouter, bitrix=bitrix)
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
    messages = _format_alina_messages(by_manager, since, until)

    # Первый чанк — в место «Хадсон собирает…», остальные отдельными сообщениями
    await wait.edit_text(messages[0], disable_web_page_preview=True)
    for extra in messages[1:]:
        await callback.message.answer(extra, disable_web_page_preview=True)
