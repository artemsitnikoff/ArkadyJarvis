"""Мисис Хадсон — рассылка уведомлений + постановка Jira-задач.

После того как hudson_analyzer.build_reports вернул per-dev отчёты, этот модуль
формирует и шлёт:
1. Каждому менеджеру — сводку по его разработчикам (часы, плохие комменты,
   список того что попадёт в задачи на подтверждение)
2. Алине Васьковой (РОП P&Q, HUDSON_DEPT_HEAD_BITRIX_ID) — общую сводку
3. В Jira-проект «PQ»:
   - На каждого менеджера: task «Подтвердить внутренние часы за неделю
     по разработчикам X, Y, Z»
   - На каждого разработчика с total < 32h: task «Поставить отгул
     разработчику X (X.Yh за неделю)»
"""
import html
import logging
from datetime import date

from aiogram import Bot

from app.config import settings
from app.db import get_db, get_user_by_bitrix_id
from app.services.ai_client import AIClient
from app.services.hudson_analyzer import (
    INTERNAL_HOURS_WARN,
    WEEKLY_HOURS_NORM,
    DevReport,
)
from app.services.jira_client import JiraClient

logger = logging.getLogger("arkadyjarvis")

PQ_PROJECT_KEY = "PQ"  # «Стратегия и развитие департамента Production&Quality»

MOTIVATION_FALLBACK = (
    "Команда P&Q, новая неделя — погнали! Подтягиваем хвосты, внутреннее "
    "тушим, внешним зажигаем 🔥"
)


async def _generate_motivation(
    ai_client: AIClient,
    by_manager: dict[str, list[DevReport]],
) -> str:
    """Уникальная мотивационная фраза через Claude CLI (subscription).
    1 вызов в неделю — на цену subscription'а не влияет."""
    total_devs = sum(len(rs) for rs in by_manager.values())
    total_hours = sum(r.total_hours for rs in by_manager.values() for r in rs)
    intern = sum(r.internal_hours for rs in by_manager.values() for r in rs)
    under = sum(1 for rs in by_manager.values() for r in rs if r.is_under_norm)
    bad = sum(len(r.bad_comments) for rs in by_manager.values() for r in rs)

    prompt = (
        "Ты — Мисис Хадсон, AI-«комендант» департамента Production&Quality "
        "компании Digital Clouds (IT-аутсорсинг, ~130 человек, Новосибирск). "
        "Команда: 4 менеджера и их разработчики (WEB-ПиК).\n\n"
        f"Цифры за прошлую неделю: разрабов {total_devs}, всего часов "
        f"{total_hours:.0f}h, из них внутренних {intern:.0f}h, "
        f"под нормой 32h было {under} человек, плохих комментариев к worklog "
        f"{bad}.\n\n"
        "Сгенерируй ОДНУ короткую (1-2 предложения, не более 200 символов) "
        "оригинальную мотивирующую фразу для понедельника — чтобы команда "
        "взбодрилась и зашла в новую неделю с настроением.\n"
        "Тон: тёплый, по-человечески, без канцеляризмов, можно один уместный "
        "эмодзи. Без обращения «Здравствуйте» и без подписи. Не повторяй "
        "цифры дословно — лишь их дух/настроение.\n"
        "Верни только саму фразу, без кавычек и без префиксов."
    )
    try:
        phrase = await ai_client.complete(prompt, timeout=60)
        phrase = phrase.strip().strip('"').strip("«»").strip()
        if phrase:
            return phrase
    except Exception as e:
        logger.warning("Hudson motivation generation failed: %s", e)
    return MOTIVATION_FALLBACK


def _dev_status(rep: DevReport) -> tuple[str, str]:
    """Возвращает (flag, причина-в-скобках). 🏖 если в отпуске (норма не применяется).
    Red если ниже нормы ИЛИ внутр > 8h."""
    if rep.on_leave:
        return "🏖", f" ({rep.absence}, пропущен)"
    reasons: list[str] = []
    if rep.is_under_norm:
        reasons.append(f"<{WEEKLY_HOURS_NORM:.0f}h недобор")
    if rep.internal_hours > INTERNAL_HOURS_WARN:
        reasons.append(f"внутр >{INTERNAL_HOURS_WARN:.0f}h")
    flag = "🔴" if reasons else "🟢"
    tag = f" ({', '.join(reasons)})" if reasons else ""
    return flag, tag


def _jira_link(issue_key: str) -> str:
    """HTML-ссылка на Jira-issue."""
    base = settings.jira_url.rstrip("/")
    return f'<a href="{base}/browse/{issue_key}">{issue_key}</a>'


def _format_dev_block(rep: DevReport) -> str:
    """HTML-блок для одного разработчика. Все плохие комменты до конца,
    с кликабельными ссылками на Jira."""
    flag, reason_tag = _dev_status(rep)
    name = html.escape(rep.name)
    line1 = (
        f"{flag} <b>{name}</b>: {rep.total_hours:.1f}h/<b>{rep.internal_hours:.1f}h</b>"
        f"{reason_tag}"
    )
    bits = [line1]
    if rep.is_under_norm:
        bits.append(
            f"   ⚠️ ниже нормы {WEEKLY_HOURS_NORM:.0f}h — поставим задачу на отгул",
        )
    if rep.bad_comments:
        bits.append(
            f"   💬 плохих комментариев: {len(rep.bad_comments)}/{len(rep.entries)}"
        )
        for entry, reason in rep.bad_comments:
            c = html.escape(entry.comment or "(пусто)")
            r = html.escape(reason)
            bits.append(
                f"     • {_jira_link(entry.issue_key)} "
                f"({entry.hours:.1f}h): «{c}» — {r}"
            )
    return "\n".join(bits)


TG_MAX = 4000  # Telegram cap 4096, оставляем запас на HTML overhead


def _format_manager_messages(
    manager: str, reports: list[DevReport], since: date, until: date,
) -> list[str]:
    """Возвращает список сообщений (≤ TG_MAX каждое). Сплит — между разработчиками."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    header = (
        f"#хадсон_{period.replace('.', '_').replace('–', '_')}\n"
        f"📋 <b>Недельный отчёт {period}</b>\n"
        f"Менеджер: <b>{html.escape(manager)}</b>\n"
        f"Разработчиков: {len(reports)}"
    )
    blocks = [_format_dev_block(r) for r in sorted(reports, key=lambda x: x.name)]

    messages: list[str] = []
    current = header
    for blk in blocks:
        candidate = current + "\n\n" + blk
        if len(candidate) <= TG_MAX:
            current = candidate
        else:
            messages.append(current)
            current = blk
    if current:
        messages.append(current)
    return messages


def _format_alina_messages(
    by_manager: dict[str, list[DevReport]], since: date, until: date,
) -> list[str]:
    """Сводка для Алины: per-manager группировка, под каждым разработчиком —
    весь его плохой лог (как у менеджера). Возвращает список chunk'ов ≤ TG_MAX."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    header = (
        f"#хадсон_сводка_{period.replace('.', '_').replace('–', '_')}\n"
        f"📊 <b>Хадсон: сводка P&amp;Q за {period}</b>"
    )

    messages: list[str] = []
    current = header
    for mgr in sorted(by_manager):
        reps = sorted(by_manager[mgr], key=lambda x: x.name)
        total = sum(r.total_hours for r in reps)
        intern = sum(r.internal_hours for r in reps)
        under_norm = [r for r in reps if r.is_under_norm]
        bad = sum(len(r.bad_comments) for r in reps)
        mgr_header = (
            f"\n— <b>{html.escape(mgr)}</b> — "
            f"{len(reps)} разрабов · {total:.0f}h всего · внутр {intern:.0f}h · "
            f"под нормой {len(under_norm)} · плохих коммов {bad}"
        )
        # Сначала пробуем добавить mgr-header целиком
        candidate = current + "\n" + mgr_header
        if len(candidate) > TG_MAX:
            messages.append(current)
            current = mgr_header
        else:
            current = candidate

        for r in reps:
            blk = _format_dev_block(r)
            candidate = current + "\n\n" + blk
            if len(candidate) <= TG_MAX:
                current = candidate
            else:
                messages.append(current)
                current = blk
    if current:
        messages.append(current)
    return messages


# Backward compat alias for bot button (hudson router)
def _format_alina_summary(
    by_manager: dict[str, list[DevReport]], since: date, until: date,
) -> str:
    """Однострочная склейка для UI (кнопка). Шлёт первый чанк — остальное
    обрежется на стороне отправителя при > 4000."""
    return _format_alina_messages(by_manager, since, until)[0]


async def _manager_bitrix_id(manager_name: str) -> int | None:
    db = get_db()
    async with db.execute(
        "SELECT manager_bitrix_id FROM hudson_managers WHERE manager_name = ? LIMIT 1",
        (manager_name,),
    ) as cur:
        row = await cur.fetchone()
        return int(row[0]) if row and row[0] else None


async def _manager_jira_username(manager_name: str) -> str | None:
    db = get_db()
    async with db.execute(
        "SELECT manager_jira_username FROM hudson_managers "
        "WHERE manager_name = ? LIMIT 1",
        (manager_name,),
    ) as cur:
        row = await cur.fetchone()
        return row[0] if row and row[0] else None


def _build_internal_breakdown(reports: list[DevReport]) -> str:
    """Plain-text блок «Внутренние часы по задачам». Группировка per-dev,
    внутри — суммирование по issue_key. Issue-keys Jira автолинкует."""
    pieces: list[str] = []
    for r in sorted(reports, key=lambda x: x.name):
        if not r.internal_entries:
            continue
        # суммируем по issue
        by_issue: dict[str, tuple[float, list[str]]] = {}
        for e in r.internal_entries:
            hours, comments = by_issue.get(e.issue_key, (0.0, []))
            c = (e.comment or "").strip().replace("\n", " ")
            if c:
                comments.append(c)
            by_issue[e.issue_key] = (hours + e.hours, comments)
        pieces.append(f"\n== {r.name} ({r.internal_hours:.1f}h всего) ==")
        for ikey, (h, comments) in sorted(
            by_issue.items(), key=lambda kv: -kv[1][0],
        ):
            joined = "; ".join(comments) if comments else "(без комментариев)"
            pieces.append(f"* {ikey} — {h:.2f}h — {joined}")
    if not pieces:
        return ""
    return "\n\nВнутренние часы по задачам:\n" + "\n".join(pieces)


def _build_bad_comments_section(reports: list[DevReport]) -> str:
    """Plain-text блок «Плохие комментарии» по всем разработчикам менеджера.
    Issue-keys типа PQ-918 Jira автолинкует в описании задачи."""
    pieces: list[str] = []
    for r in sorted(reports, key=lambda x: x.name):
        if not r.bad_comments:
            continue
        pieces.append(f"\n== {r.name} ({len(r.bad_comments)}/{len(r.entries)}) ==")
        for entry, reason in r.bad_comments:
            c = (entry.comment or "(пусто)").replace("\n", " ")
            pieces.append(
                f"* {entry.issue_key} ({entry.hours:.2f}h): «{c}» — {reason}"
            )
    if not pieces:
        return ""
    return "\n\nПлохие комментарии за неделю:\n" + "\n".join(pieces)


async def _create_pq_tasks(
    by_manager: dict[str, list[DevReport]], since: date, until: date,
) -> list[str]:
    """Постановка задач в Jira PQ. Возвращает список созданных ключей.
    Assignee = менеджер из hudson_managers.manager_jira_username."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    created: list[str] = []
    try:
        async with JiraClient() as jira:
            for mgr, reports in by_manager.items():
                mgr_assignee = await _manager_jira_username(mgr)
                if not mgr_assignee:
                    logger.warning(
                        "Hudson Jira: нет manager_jira_username у %s — assignee "
                        "уйдёт в дефолт проекта PQ", mgr,
                    )

                devs_with_internal = [r for r in reports if r.internal_hours > 0]
                if devs_with_internal:
                    devs_str = ", ".join(
                        f"{r.name} ({r.internal_hours:.1f}h)"
                        for r in devs_with_internal
                    )
                    summary = f"[Хадсон {period}] Подтвердить внутренние часы — {mgr}"
                    desc = (
                        f"Подтвердить, что внутренние часы корректны для:\n\n"
                        f"{devs_str}\n\n"
                        f"Период: {since} → {until}.\n"
                        f"Если часы списаны верно — закрыть задачу. "
                        f"Если ошибочно — попросить разработчика переписать "
                        f"на внешний проект."
                        f"{_build_internal_breakdown(devs_with_internal)}"
                        f"{_build_bad_comments_section(reports)}"
                    )
                    try:
                        issue = await jira.create_issue(
                            PQ_PROJECT_KEY, summary, desc,
                            assignee_name=mgr_assignee,
                        )
                        created.append(issue["key"])
                    except Exception as e:
                        logger.warning("Hudson Jira create (mgr %s) failed: %s", mgr, e)

                for r in reports:
                    if r.is_under_norm:
                        summary = (
                            f"[Хадсон {period}] Отгул: {r.name} "
                            f"({r.total_hours:.1f}h за неделю)"
                        )
                        desc = (
                            f"За период {since} → {until} разработчик {r.name} "
                            f"списал в Jira {r.total_hours:.1f}h "
                            f"(норма {WEEKLY_HOURS_NORM:.0f}h).\n\n"
                            f"Менеджер: {mgr}.\n"
                            f"Действие: оформить отгул или прокомментировать причину."
                        )
                        try:
                            issue = await jira.create_issue(
                                PQ_PROJECT_KEY, summary, desc,
                                assignee_name=mgr_assignee,
                            )
                            created.append(issue["key"])
                        except Exception as e:
                            logger.warning(
                                "Hudson Jira create (dev %s) failed: %s", r.name, e,
                            )
    except Exception as e:
        logger.error("Hudson Jira context failed: %s", e, exc_info=True)
    return created


async def _format_group_message(
    by_manager: dict[str, list[DevReport]],
    since: date,
    until: date,
    manager_telegrams: dict[str, tuple[int, str]],
    ai_client: AIClient,
) -> str:
    """Сообщение в общую группу: per-manager статистика + тэг менеджеров +
    Sonnet-сгенерированная мотивационная фраза."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    lines = [
        f"📊 <b>Мисис Хадсон · недельный отчёт {period}</b>",
        "",
    ]
    for mgr in sorted(by_manager):
        reps = by_manager[mgr]
        total = sum(r.total_hours for r in reps)
        intern = sum(r.internal_hours for r in reps)
        under = sum(1 for r in reps if r.is_under_norm)
        bad = sum(len(r.bad_comments) for r in reps)

        tg = manager_telegrams.get(mgr)
        if tg:
            tg_id, name = tg
            mention = f'<a href="tg://user?id={tg_id}">{html.escape(name or mgr)}</a>'
        else:
            mention = f"<b>{html.escape(mgr)}</b>"
        lines.append(
            f"{mention} — {len(reps)} разрабов · {total:.0f}h всего · "
            f"внутр {intern:.0f}h · отгулов {under} · плохих коммов {bad}"
        )
    lines.append("")
    lines.append(await _generate_motivation(ai_client, by_manager))
    return "\n".join(lines)


async def notify(
    reports: list[DevReport],
    since: date,
    until: date,
    bot: Bot,
    ai_client: AIClient,
    dry_run: bool = False,
) -> dict:
    """Главная точка входа: рассылка менеджерам + сводка Алине + Jira-задачи.
    При dry_run=True не отправляет Telegram-сообщения и не создаёт Jira."""
    by_manager: dict[str, list[DevReport]] = {}
    for r in reports:
        by_manager.setdefault(r.manager_name, []).append(r)

    sent_managers = 0
    sent_alina = False
    sent_group = False
    # Кешируем (tg_id, display_name) каждого менеджера — пригодится для тэга в группе
    manager_telegrams: dict[str, tuple[int, str]] = {}
    for mgr, reps in by_manager.items():
        manager_bitrix_id = await _manager_bitrix_id(mgr)
        if not manager_bitrix_id:
            logger.warning("Hudson notify: no Bitrix ID for manager %s", mgr)
            continue
        user = await get_user_by_bitrix_id(manager_bitrix_id)
        if not user:
            logger.warning(
                "Hudson notify: manager %s (bitrix_id=%s) ещё не в users — пропуск",
                mgr, manager_bitrix_id,
            )
            continue
        manager_telegrams[mgr] = (
            int(user["telegram_id"]),
            user.get("display_name") or mgr,
        )
        messages = _format_manager_messages(mgr, reps, since, until)
        if dry_run:
            logger.info(
                "[DRY-RUN] would send %d message(s) to %s (tg=%s)",
                len(messages), mgr, user["telegram_id"],
            )
            sent_managers += 1
            continue
        try:
            for text in messages:
                await bot.send_message(
                    user["telegram_id"], text, disable_web_page_preview=True,
                )
            sent_managers += 1
        except Exception as e:
            logger.error("Hudson notify: send to %s failed: %s", mgr, e)

    alina = await get_user_by_bitrix_id(settings.hudson_dept_head_bitrix_id)
    if alina:
        alina_msgs = _format_alina_messages(by_manager, since, until)
        if dry_run:
            logger.info(
                "[DRY-RUN] would send %d message(s) Alina summary (tg=%s)",
                len(alina_msgs), alina["telegram_id"],
            )
            sent_alina = True
        else:
            try:
                for text in alina_msgs:
                    await bot.send_message(
                        alina["telegram_id"], text, disable_web_page_preview=True,
                    )
                sent_alina = True
            except Exception as e:
                logger.error("Hudson notify: send to Алина failed: %s", e)
    else:
        logger.warning(
            "Hudson notify: РОП (bitrix_id=%s) ещё не в users",
            settings.hudson_dept_head_bitrix_id,
        )

    # Group message — с тэгом менеджеров и AI-мотивацией (Claude CLI subscription)
    if settings.hudson_chat_id:
        group_text = await _format_group_message(
            by_manager, since, until, manager_telegrams, ai_client,
        )
        if dry_run:
            logger.info(
                "[DRY-RUN] would send group msg to %s (%d managers tagged)",
                settings.hudson_chat_id, len(manager_telegrams),
            )
            sent_group = True
        else:
            try:
                await bot.send_message(
                    settings.hudson_chat_id, group_text,
                    disable_web_page_preview=True,
                )
                sent_group = True
            except Exception as e:
                logger.error(
                    "Hudson notify: send to group %s failed: %s",
                    settings.hudson_chat_id, e,
                )

    created_keys: list[str] = []
    if dry_run:
        logger.info("[DRY-RUN] skipping Jira PQ task creation")
    else:
        created_keys = await _create_pq_tasks(by_manager, since, until)

    logger.info(
        "Hudson notify done (dry=%s): managers=%d alina=%s group=%s jira_keys=%s",
        dry_run, sent_managers, sent_alina, sent_group, created_keys,
    )
    return {
        "managers_sent": sent_managers,
        "alina_sent": sent_alina,
        "group_sent": sent_group,
        "jira_keys": created_keys,
        "dry_run": dry_run,
    }
