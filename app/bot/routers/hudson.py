"""Мисис Хадсон — кнопка в меню для показа текущего состояния worklog'ов.

Показывает сводную таблицу как у Алины (без рассылки и без Jira). Доступ
только TG IDs из HUDSON_ALLOWED (я + менеджеры P&Q).
"""
import html
import logging
from datetime import date, timedelta

from aiogram import F, Router
from aiogram.types import BufferedInputFile, CallbackQuery

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
    """Отчёт Хадсона за последние 7 дней — отправляется в ЛИЧКУ пользователю,
    чтобы не светить в группе. Доступ: HUDSON_ALLOWED (я + менеджеры)."""
    if callback.from_user.id not in HUDSON_ALLOWED:
        await callback.answer("🏠 Доступ закрыт", show_alert=True)
        return
    await callback.answer("🏠 Сейчас пришлю отчёт тебе в личку", show_alert=False)

    from app.services.hudson_analyzer import build_reports
    from app.services.hudson_notifier import (
        _build_all_worklogs_md,
        _build_bad_comments_md,
        _build_internal_md,
        _format_alina_messages,
        _manager_full_names,
    )

    user_id = callback.from_user.id
    bot = callback.bot

    try:
        wait = await bot.send_message(
            user_id,
            "🏠 Хадсон собирает worklog'и WEB-ПиК за неделю, "
            "+ Haiku-классификация комментариев. Ждать минуту…",
        )
    except Exception as e:
        # Юзер не нажимал /start у бота, поэтому DM закрыт
        logger.warning("Hudson button: cannot DM user %s: %s", user_id, e)
        await callback.message.answer(
            "❌ Открой со мной личку (нажми /start в DM боту), "
            "и попробуй ещё раз — отчёт большой, чтобы не светить его в группе.",
        )
        return

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

    messages = await _format_alina_messages(by_manager, since, until)
    await wait.edit_text(messages[0], disable_web_page_preview=True)
    for extra in messages[1:]:
        await bot.send_message(user_id, extra, disable_web_page_preview=True)

    full_names = await _manager_full_names(list(by_manager.keys()))
    period_tag = f"{since.isoformat()}_{until.isoformat()}"
    bad_md = _build_bad_comments_md(by_manager, full_names, since, until).encode("utf-8")
    intl_md = _build_internal_md(by_manager, full_names, since, until).encode("utf-8")
    all_md = _build_all_worklogs_md(by_manager, full_names, since, until).encode("utf-8")
    await bot.send_document(
        user_id,
        BufferedInputFile(bad_md, filename=f"bad_comments_{period_tag}.md"),
    )
    await bot.send_document(
        user_id,
        BufferedInputFile(intl_md, filename=f"internal_hours_{period_tag}.md"),
    )
    await bot.send_document(
        user_id,
        BufferedInputFile(all_md, filename=f"all_worklogs_{period_tag}.md"),
    )
