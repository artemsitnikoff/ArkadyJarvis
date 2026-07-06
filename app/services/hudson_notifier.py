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
from aiogram.types import BufferedInputFile

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
    """Возвращает (flag, причина-в-скобках). 🏖 если ВСЯ неделя в отпуске
    (норма обнулилась). Иначе обычная логика с поправкой нормы на дни отлучки."""
    if rep.on_leave:
        return "🏖", f" ({rep.absence or 'отлучка'}, пропущен)"
    reasons: list[str] = []
    if rep.absence:  # частичная отлучка (1-4 раб. дня)
        reasons.append(rep.absence)
    if rep.is_under_norm:
        reasons.append(f"недобор до {rep.weekly_norm:.0f}h")
    if rep.internal_hours > INTERNAL_HOURS_WARN:
        reasons.append(f"внутр выше {INTERNAL_HOURS_WARN:.0f}h")
    is_red = rep.is_under_norm or rep.internal_hours > INTERNAL_HOURS_WARN
    flag = "🔴" if is_red else "🟢"
    tag = f" ({', '.join(reasons)})" if reasons else ""
    return flag, tag


def _jira_link(issue_key: str) -> str:
    """HTML-ссылка на Jira-issue."""
    base = settings.jira_url.rstrip("/")
    return f'<a href="{base}/browse/{issue_key}">{issue_key}</a>'


def _format_dev_block(rep: DevReport) -> str:
    """Компактный HTML-блок per-dev: статус + счётчик плохих.
    Детали (сами комменты и часы по задачам) — в прикреплённых .md."""
    flag, reason_tag = _dev_status(rep)
    name = html.escape(rep.name)
    line1 = (
        f"{flag} <b>{name}</b>: {rep.total_hours:.1f}h/<b>{rep.internal_hours:.1f}h</b>"
        f"{reason_tag}"
    )
    bits = [line1]
    if rep.downtime_hours > 0:
        bits.append(
            f"   💤 простой: {rep.downtime_hours:.1f}h — нужна приёмка менеджером (см. .md)",
        )
    if rep.is_under_norm:
        bits.append(
            f"   ⚠️ ниже нормы {rep.weekly_norm:.0f}h — поставим задачу на отгул",
        )
    if rep.bad_comments:
        bits.append(
            f"   💬 плохих коммов: {len(rep.bad_comments)}/{len(rep.entries)} "
            f"(см. .md)"
        )
    return "\n".join(bits)


def _build_bad_comments_md(
    by_manager: dict[str, list[DevReport]],
    full_names: dict[str, str],
    since: date, until: date,
) -> str:
    """Markdown-документ со всеми плохими комментариями."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    base = settings.jira_url.rstrip("/")
    out = [f"# Плохие комментарии за {period}", ""]
    has_any = False
    for mgr in sorted(by_manager):
        mgr_full = full_names.get(mgr, mgr)
        for r in sorted(by_manager[mgr], key=lambda x: x.name):
            if not r.bad_comments:
                continue
            has_any = True
            out.append(
                f"## {mgr_full} → {r.name} "
                f"({len(r.bad_comments)}/{len(r.entries)})"
            )
            out.append("")
            for entry, reason in r.bad_comments:
                c = (entry.comment or "(пусто)").replace("\n", " ")
                link = f"[{entry.issue_key}]({base}/browse/{entry.issue_key})"
                out.append(
                    f"- {link} ({entry.hours:.2f}h): «{c}» — {reason}"
                )
            out.append("")
    if not has_any:
        out.append("_Нет плохих комментариев за период — красавцы!_")
    return "\n".join(out)


def _build_internal_md(
    by_manager: dict[str, list[DevReport]],
    full_names: dict[str, str],
    since: date, until: date,
) -> str:
    """Markdown-документ с разбивкой внутренних часов по задачам."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    base = settings.jira_url.rstrip("/")
    out = [f"# Внутренние часы по задачам за {period}", ""]
    has_any = False
    for mgr in sorted(by_manager):
        mgr_full = full_names.get(mgr, mgr)
        for r in sorted(by_manager[mgr], key=lambda x: x.name):
            if not r.internal_entries:
                continue
            has_any = True
            out.append(f"## {mgr_full} → {r.name} ({r.internal_hours:.1f}h всего)")
            out.append("")
            by_issue: dict[str, tuple[float, list[str]]] = {}
            for e in r.internal_entries:
                hours, comments = by_issue.get(e.issue_key, (0.0, []))
                c = (e.comment or "").strip().replace("\n", " ")
                if c:
                    comments.append(c)
                by_issue[e.issue_key] = (hours + e.hours, comments)
            for ikey, (h, comments) in sorted(
                by_issue.items(), key=lambda kv: -kv[1][0],
            ):
                link = f"[{ikey}]({base}/browse/{ikey})"
                joined = "; ".join(comments) if comments else "(без комментариев)"
                out.append(f"- {link} — {h:.2f}h — {joined}")
            out.append("")
    if not has_any:
        out.append("_Внутренних часов нет — все на внешних проектах._")
    return "\n".join(out)


def _build_downtime_md(
    by_manager: dict[str, list[DevReport]],
    full_names: dict[str, str],
    since: date, until: date,
) -> str:
    """Markdown-документ с часами ПРОСТОЯ (DCBE и пр.) по разработчикам.
    Менеджер должен принять простой (задача в Jira PQ)."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    base = settings.jira_url.rstrip("/")
    out = [
        f"# Простой за {period}",
        "",
        "_Часы, списанные на инстансы простоя (DCBE/DCFE/DCAP/DCQQ/DCDE — «Простой "
        "<роль> Digital Clouds»). Это НЕ внутренняя работа: в норму идут, но менеджер "
        "должен принять простой (по каждому — задача в Jira PQ)._",
        "",
    ]
    has_any = False
    total_all = 0.0
    for mgr in sorted(by_manager):
        mgr_full = full_names.get(mgr, mgr)
        for r in sorted(by_manager[mgr], key=lambda x: x.name):
            if not r.downtime_entries:
                continue
            has_any = True
            total_all += r.downtime_hours
            out.append(f"## {mgr_full} → {r.name} ({r.downtime_hours:.1f}h простоя)")
            out.append("")
            _emit_worklog_lines(out, r.downtime_entries, base, show_project=True)
            out.append("")
    if not has_any:
        out.append("_Простоя за период нет — все при деле 🎉_")
    else:
        out.insert(4, f"**Всего простоя за неделю: {total_all:.1f}h**\n")
    return "\n".join(out)


def _emit_worklog_lines(
    out: list[str], entries: list, base: str, *, show_project: bool = False,
) -> None:
    """Печатает каждую worklog-запись отдельной строкой, хронологически.
    show_project=True — дописывает ключ проекта (для вне-аудитных записей)."""
    for e in sorted(entries, key=lambda x: (x.started_at, x.issue_key)):
        d = e.started_at.strftime("%d.%m")
        link = f"[{e.issue_key}]({base}/browse/{e.issue_key})"
        summ = (e.issue_summary or "").strip().replace("\n", " ")
        comment = (e.comment or "").strip().replace("\n", " ") or "(без комментария)"
        proj = f" · проект `{e.project_key}`" if show_project else ""
        head = f"{link} {summ}".strip()
        out.append(f"- {d} {head} — {e.hours:.2f}h — «{comment}»{proj}")


def _build_all_worklogs_md(
    by_manager: dict[str, list[DevReport]],
    full_names: dict[str, str],
    since: date, until: date,
) -> str:
    """Markdown-документ со ВСЕМИ worklog'ами каждого разработчика — для сверки.
    Делит на «✅ учтено» (проект в наборе аудита) и «⚠️ не учтено» (проект вне
    аудита — часы не считаются). Закрывает жалобы «мой лог не учтён»."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    base = settings.jira_url.rstrip("/")
    out = [
        f"# Все worklog'и за {period} (для сверки)",
        "",
        "_Полный список списанного времени по каждому разработчику._",
        "- **✅ учтено** — проект входит в аудит часов (направление WEB-ПиК "
        "+ доп. проекты вроде COZYHOME). Эти часы идут в норму и в отчёт.",
        "- **💤 простой** — инстансы простоя (DCBE и пр.): в норму идут, но это не "
        "работа — менеджер принимает простой отдельно.",
        "- **⚠️ не учтено** — проект вне аудита: часы по нему **не считаются**. "
        "Если такой проект должен учитываться — напишите, добавим в аудит.",
        "",
    ]
    has_any = False
    for mgr in sorted(by_manager):
        mgr_full = full_names.get(mgr, mgr)
        for r in sorted(by_manager[mgr], key=lambda x: x.name):
            if not r.entries and not r.downtime_entries and not r.out_of_scope_entries:
                continue
            has_any = True
            notes = ""
            if r.downtime_hours > 0:
                notes += f" · 💤 простой {r.downtime_hours:.2f}h"
            if r.out_of_scope_hours > 0:
                notes += f" · ⚠️ не учтено {r.out_of_scope_hours:.2f}h"
            out.append(f"## {mgr_full} → {r.name}")
            out.append("")
            out.append(
                f"**Учтено {r.total_hours:.2f}h** "
                f"(внутр {r.internal_hours:.2f}h / внеш {r.external_hours:.2f}h)"
                f"{notes}"
            )
            out.append("")
            if r.entries:
                out.append("### ✅ Учтено")
                _emit_worklog_lines(out, r.entries, base)
                out.append("")
            if r.downtime_entries:
                out.append("### 💤 Простой")
                _emit_worklog_lines(out, r.downtime_entries, base, show_project=True)
                out.append("")
            if r.out_of_scope_entries:
                out.append("### ⚠️ Не учтено (проект вне аудита)")
                _emit_worklog_lines(
                    out, r.out_of_scope_entries, base, show_project=True,
                )
                out.append("")
    if not has_any:
        out.append("_За период никто ничего не списал._")
    return "\n".join(out)


def _collect_unknown_projects(reports: list[DevReport]) -> dict[str, dict]:
    """Агрегирует out-of-scope записи всех разработчиков по project_key.

    Возвращает {key: {name, hours, entries, devs:set[str], sample_issue}}.
    Это проекты, по которым списывали часы, но которых нет ни в WEB-ПиК, ни в
    EXTRA_AUDIT_PROJECTS, ни в простое — кандидаты на добавление в аудит."""
    agg: dict[str, dict] = {}
    for r in reports:
        for e in r.out_of_scope_entries:
            info = agg.setdefault(
                e.project_key,
                {"name": "", "hours": 0.0, "entries": 0,
                 "devs": set(), "sample_issue": e.issue_key},
            )
            if not info["name"]:
                info["name"] = getattr(e, "project_name", "") or ""
            info["hours"] += e.hours
            info["entries"] += 1
            info["devs"].add(r.name)
    return agg


def _build_unknown_projects_md(
    unknown: dict[str, dict], since: date, until: date,
) -> str:
    """Markdown-todo по неизвестным проектам — чтобы потихоньку вносить их в
    EXTRA_AUDIT_PROJECTS. По каждому: ключ, название, часы, разработчики, пример
    задачи и готовая строка для вставки. Плюс общий блок для копипаста.
    Отсортировано по убыванию часов (сверху — где больше времени сожгли)."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    base = settings.jira_url.rstrip("/")
    out = [
        f"# Неизвестные проекты за {period}",
        "",
        "_Проекты, по которым списывали часы, но которых нет в аудите (ни WEB-ПиК, "
        "ни EXTRA_AUDIT_PROJECTS, ни простой). Часы по ним **не учтены**. Для "
        "каждого реши: внешний (`0`) или внутренний (`1`) — и добавь строку в "
        "`EXTRA_AUDIT_PROJECTS` в `app/services/hudson_analyzer.py`. Сверху — где "
        "больше часов._",
        "",
    ]
    ranked = sorted(
        unknown.items(), key=lambda kv: (-kv[1]["hours"], kv[0]),
    )
    for key, info in ranked:
        name = info["name"] or "(название не пришло из Jira)"
        devs = ", ".join(sorted(info["devs"]))
        out.append(f"## {key} — {name}")
        out.append(
            f"- **{info['hours']:.2f}h** · {info['entries']} зап. · {devs}"
        )
        out.append(
            f"- пример: [{info['sample_issue']}]"
            f"({base}/browse/{info['sample_issue']})"
        )
        out.append(f'- вставить: `"{key}": 0,  # {name}`')
        out.append("")
    out.append("---")
    out.append("")
    out.append(
        "### Всё блоком для `EXTRA_AUDIT_PROJECTS` (проставь `0`=внешний / `1`=внутренний):"
    )
    out.append("")
    out.append("```python")
    for key, info in ranked:
        name = info["name"] or "?"
        out.append(f'    "{key}": 0,  # {name} — {info["hours"]:.2f}h')
    out.append("```")
    return "\n".join(out)


TG_MAX = 3800  # Telegram cap 4096, оставляем запас на HTML-сущности (&amp; и т.п.)


def _split_block(blk: str, limit: int = TG_MAX) -> list[str]:
    """Если блок >limit — делим по строкам. Возвращает список chunk'ов."""
    if len(blk) <= limit:
        return [blk]
    lines = blk.split("\n")
    chunks: list[str] = []
    cur = ""
    for line in lines:
        candidate = (cur + "\n" + line) if cur else line
        if len(candidate) <= limit:
            cur = candidate
        else:
            if cur:
                chunks.append(cur)
            cur = line if len(line) <= limit else line[:limit]
    if cur:
        chunks.append(cur)
    return chunks


def _append_block_to_messages(
    blk: str, current: str, messages: list[str],
) -> str:
    """Пытается дописать блок к текущему сообщению; если не влезает — делит блок
    и переносит на следующее сообщение. Возвращает обновлённое `current`."""
    for sub in _split_block(blk):
        candidate = (current + "\n\n" + sub) if current else sub
        if len(candidate) <= TG_MAX:
            current = candidate
        else:
            if current:
                messages.append(current)
            current = sub
    return current


async def _format_manager_messages(
    manager: str, reports: list[DevReport], since: date, until: date,
) -> list[str]:
    """Возвращает список сообщений (≤ TG_MAX каждое). Сплит — между разработчиками."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    display_name = await _manager_full_name(manager)
    header = (
        f"#хадсон_{period.replace('.', '_').replace('–', '_')}\n"
        f"📋 <b>Недельный отчёт {period}</b>\n"
        f"Менеджер: <b>{html.escape(display_name)}</b>\n"
        f"Разработчиков: {len(reports)}"
    )
    blocks = [_format_dev_block(r) for r in sorted(reports, key=lambda x: x.name)]

    messages: list[str] = []
    current = header
    for blk in blocks:
        current = _append_block_to_messages(blk, current, messages)
    if current:
        messages.append(current)
    return messages


async def _format_alina_messages(
    by_manager: dict[str, list[DevReport]], since: date, until: date,
) -> list[str]:
    """Сводка для Алины: per-manager группировка, под каждым разработчиком —
    весь его плохой лог (как у менеджера). Возвращает список chunk'ов ≤ TG_MAX."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    full_names = await _manager_full_names(list(by_manager.keys()))
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
        downtime = sum(r.downtime_hours for r in reps)
        under_norm = [r for r in reps if r.is_under_norm]
        bad = sum(len(r.bad_comments) for r in reps)
        mgr_display = full_names.get(mgr, mgr)
        downtime_seg = f" · 💤 простой {downtime:.0f}h" if downtime > 0 else ""
        mgr_header = (
            f"— <b>{html.escape(mgr_display)}</b> — "
            f"{len(reps)} разрабов · {total:.0f}h всего · внутр {intern:.0f}h"
            f"{downtime_seg} · под нормой {len(under_norm)} · плохих коммов {bad}"
        )
        current = _append_block_to_messages(mgr_header, current, messages)
        for r in reps:
            blk = _format_dev_block(r)
            current = _append_block_to_messages(blk, current, messages)
    if current:
        messages.append(current)
    return messages




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


async def _manager_full_name(manager_name: str) -> str:
    """Возвращает 'Имя Фамилия' для менеджера, либо его фамилию-ключ если не нашли."""
    db = get_db()
    async with db.execute(
        "SELECT manager_full_name FROM hudson_managers "
        "WHERE manager_name = ? LIMIT 1",
        (manager_name,),
    ) as cur:
        row = await cur.fetchone()
        return (row[0] if row and row[0] else manager_name) or manager_name


async def _manager_full_names(manager_names: list[str]) -> dict[str, str]:
    """Батчем тянет 'Имя Фамилия' для списка менеджеров."""
    result: dict[str, str] = {}
    db = get_db()
    placeholders = ",".join("?" * len(manager_names))
    async with db.execute(
        f"SELECT DISTINCT manager_name, manager_full_name FROM hudson_managers "
        f"WHERE manager_name IN ({placeholders})",
        manager_names,
    ) as cur:
        for row in await cur.fetchall():
            result[row[0]] = row[1] or row[0]
    for m in manager_names:
        result.setdefault(m, m)
    return result


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


def _build_downtime_breakdown(reports: list[DevReport]) -> str:
    """Plain-text блок «Простой по задачам». Группировка per-dev, внутри —
    суммирование по issue_key. Issue-keys Jira автолинкует."""
    pieces: list[str] = []
    for r in sorted(reports, key=lambda x: x.name):
        if not r.downtime_entries:
            continue
        by_issue: dict[str, tuple[float, list[str]]] = {}
        for e in r.downtime_entries:
            hours, comments = by_issue.get(e.issue_key, (0.0, []))
            c = (e.comment or "").strip().replace("\n", " ")
            if c:
                comments.append(c)
            by_issue[e.issue_key] = (hours + e.hours, comments)
        pieces.append(f"\n== {r.name} ({r.downtime_hours:.1f}h простоя) ==")
        for ikey, (h, comments) in sorted(
            by_issue.items(), key=lambda kv: -kv[1][0],
        ):
            joined = "; ".join(comments) if comments else "(без комментариев)"
            pieces.append(f"* {ikey} — {h:.2f}h — {joined}")
    if not pieces:
        return ""
    return "\n\nПростой по задачам:\n" + "\n".join(pieces)


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
                    # Fallback: ищем email менеджера в Bitrix → Jira
                    bx_id = await _manager_bitrix_id(mgr)
                    if bx_id:
                        from app.services.bitrix_client import BitrixClient
                        bx = BitrixClient()
                        try:
                            r = await bx._request("user.get", {"ID": bx_id})
                            users = r.get("result") or []
                            email = (users[0].get("EMAIL") if users else "") or ""
                            if email:
                                mgr_assignee = await jira.find_user_by_email(email)
                                logger.info(
                                    "Hudson: resolved %s assignee at runtime: %s",
                                    mgr, mgr_assignee,
                                )
                        except Exception as e:
                            logger.warning("Hudson runtime resolve %s failed: %s", mgr, e)
                        finally:
                            await bx.close()
                if not mgr_assignee:
                    logger.warning(
                        "Hudson Jira: нет assignee у %s — уйдёт в дефолт PQ", mgr,
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

                # Задача на ПРИЁМКУ ПРОСТОЯ — по менеджеру, если у кого-то есть простой
                devs_with_downtime = [r for r in reports if r.downtime_hours > 0]
                if devs_with_downtime:
                    downtime_str = ", ".join(
                        f"{r.name} ({r.downtime_hours:.1f}h)"
                        for r in devs_with_downtime
                    )
                    summary = f"[Хадсон {period}] Принять простой — {mgr}"
                    desc = (
                        f"За период {since} → {until} списан простой (DCBE и пр.) у:\n\n"
                        f"{downtime_str}\n\n"
                        f"Простой — это НЕ внутренняя работа, а отсутствие задач. "
                        f"Принять простой: подтвердить, что человек реально был на "
                        f"скамейке, и при необходимости разобраться почему (нет проектов "
                        f"/ ждёт согласований / др.)."
                        f"{_build_downtime_breakdown(devs_with_downtime)}"
                    )
                    try:
                        issue = await jira.create_issue(
                            PQ_PROJECT_KEY, summary, desc,
                            assignee_name=mgr_assignee,
                        )
                        created.append(issue["key"])
                    except Exception as e:
                        logger.warning(
                            "Hudson Jira downtime (mgr %s) failed: %s", mgr, e,
                        )

                for r in reports:
                    if r.is_under_norm:
                        summary = (
                            f"[Хадсон {period}] Отгул: {r.name} "
                            f"({r.total_hours:.1f}h за неделю)"
                        )
                        desc = (
                            f"За период {since} → {until} разработчик {r.name} "
                            f"списал в Jira {r.total_hours:.1f}h "
                            f"(норма {r.weekly_norm:.0f}h).\n\n"
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


async def _format_group_messages(
    by_manager: dict[str, list[DevReport]],
    since: date,
    until: date,
    manager_telegrams: dict[str, tuple[int, str]],
    ai_client: AIClient,
) -> list[str]:
    """Полный отчёт в общую группу: краткая сводка с тэгами + мотивашка +
    per-dev блоки (часы, плохие комменты с Jira-ссылками). Сплит по TG_MAX."""
    period = f"{since.strftime('%d.%m')}–{until.strftime('%d.%m')}"
    motivation = await _generate_motivation(ai_client, by_manager)
    full_names = await _manager_full_names(list(by_manager.keys()))

    # Header: общая сводка с тэгами + мотивация
    header_lines = [
        f"📊 <b>Мисис Хадсон · недельный отчёт {period}</b>",
        "",
    ]
    for mgr in sorted(by_manager):
        reps = by_manager[mgr]
        total = sum(r.total_hours for r in reps)
        intern = sum(r.internal_hours for r in reps)
        downtime = sum(r.downtime_hours for r in reps)
        under = sum(1 for r in reps if r.is_under_norm)
        on_leave = sum(1 for r in reps if r.on_leave)
        bad = sum(len(r.bad_comments) for r in reps)
        display_name = full_names.get(mgr, mgr)
        tg = manager_telegrams.get(mgr)
        if tg:
            tg_id, _ = tg
            mention = f'<a href="tg://user?id={tg_id}">{html.escape(display_name)}</a>'
        else:
            mention = f"<b>{html.escape(display_name)}</b>"
        downtime_seg = f" · 💤 простой {downtime:.0f}h" if downtime > 0 else ""
        header_lines.append(
            f"{mention} — {len(reps)} разрабов · {total:.0f}h всего · "
            f"внутр {intern:.0f}h{downtime_seg} · отгулов {under} · "
            f"в отпуске {on_leave} · плохих коммов {bad}"
        )
    header_lines.append("")
    header_lines.append(motivation)
    header = "\n".join(header_lines)

    # Далее — per-manager + per-dev блоки (как Алине)
    messages: list[str] = []
    current = header
    for mgr in sorted(by_manager):
        reps = sorted(by_manager[mgr], key=lambda x: x.name)
        display_name = full_names.get(mgr, mgr)
        current = _append_block_to_messages(
            f"— <b>{html.escape(display_name)}</b> —", current, messages,
        )
        for r in reps:
            blk = _format_dev_block(r)
            current = _append_block_to_messages(blk, current, messages)
    if current:
        messages.append(current)
    return messages


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

    # Резолвим telegram_id всех менеджеров (для тэгов в группе и DM-рассылки)
    manager_telegrams: dict[str, tuple[int, str]] = {}
    for mgr in by_manager:
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

    # Готовим .md-файлы (одни и те же для всех получателей)
    full_names = await _manager_full_names(list(by_manager.keys()))
    period_tag = f"{since.isoformat()}_{until.isoformat()}"
    bad_md_bytes = _build_bad_comments_md(
        by_manager, full_names, since, until,
    ).encode("utf-8")
    internal_md_bytes = _build_internal_md(
        by_manager, full_names, since, until,
    ).encode("utf-8")
    all_md_bytes = _build_all_worklogs_md(
        by_manager, full_names, since, until,
    ).encode("utf-8")
    downtime_md_bytes = _build_downtime_md(
        by_manager, full_names, since, until,
    ).encode("utf-8")
    # Неизвестные проекты (out-of-scope) — отдельный .md только если они есть,
    # чтобы не слать «пустышку» каждую неделю.
    unknown_projects = _collect_unknown_projects(reports)
    unknown_md_bytes = (
        _build_unknown_projects_md(unknown_projects, since, until).encode("utf-8")
        if unknown_projects
        else b""
    )
    n_md = 4 + (1 if unknown_md_bytes else 0)

    async def _send_with_md(chat_id: int, messages: list[str]) -> None:
        for text in messages:
            await bot.send_message(chat_id, text, disable_web_page_preview=True)
        await bot.send_document(
            chat_id,
            BufferedInputFile(bad_md_bytes, filename=f"bad_comments_{period_tag}.md"),
        )
        await bot.send_document(
            chat_id,
            BufferedInputFile(
                internal_md_bytes, filename=f"internal_hours_{period_tag}.md",
            ),
        )
        await bot.send_document(
            chat_id,
            BufferedInputFile(
                downtime_md_bytes, filename=f"downtime_{period_tag}.md",
            ),
        )
        await bot.send_document(
            chat_id,
            BufferedInputFile(
                all_md_bytes, filename=f"all_worklogs_{period_tag}.md",
            ),
        )
        if unknown_md_bytes:
            await bot.send_document(
                chat_id,
                BufferedInputFile(
                    unknown_md_bytes, filename=f"unknown_projects_{period_tag}.md",
                ),
            )

    # 1) DM каждому менеджеру — только его команда
    for mgr, reps in by_manager.items():
        if mgr not in manager_telegrams:
            continue
        tg_id, _ = manager_telegrams[mgr]
        messages = await _format_manager_messages(mgr, reps, since, until)
        if dry_run:
            logger.info(
                "[DRY-RUN] would send %d msg(s) + %d .md to %s (tg=%s)",
                len(messages), n_md, mgr, tg_id,
            )
            sent_managers += 1
            continue
        try:
            await _send_with_md(tg_id, messages)
            sent_managers += 1
        except Exception as e:
            logger.error("Hudson notify: send to %s failed: %s", mgr, e)

    # 2) Алина — полный отчёт + .md
    alina = await get_user_by_bitrix_id(settings.hudson_dept_head_bitrix_id)
    if alina:
        alina_msgs = await _format_alina_messages(by_manager, since, until)
        if dry_run:
            logger.info(
                "[DRY-RUN] would send %d msg(s) + %d .md to Алина (tg=%s)",
                len(alina_msgs), n_md, alina["telegram_id"],
            )
            sent_alina = True
        else:
            try:
                await _send_with_md(int(alina["telegram_id"]), alina_msgs)
                sent_alina = True
            except Exception as e:
                logger.error("Hudson notify: send to Алина failed: %s", e)
    else:
        logger.warning(
            "Hudson notify: РОП (bitrix_id=%s) ещё не в users",
            settings.hudson_dept_head_bitrix_id,
        )

    # 3) Группа — компактная шапка с тэгами + мотивация + .md
    if settings.hudson_chat_id:
        group_msgs = await _format_group_messages(
            by_manager, since, until, manager_telegrams, ai_client,
        )
        if dry_run:
            logger.info(
                "[DRY-RUN] would send %d group msg(s) + %d .md to %s",
                len(group_msgs), n_md, settings.hudson_chat_id,
            )
            sent_group = True
        else:
            try:
                await _send_with_md(settings.hudson_chat_id, group_msgs)
                sent_group = True
            except Exception as e:
                logger.error(
                    "Hudson notify: send to group %s failed: %s",
                    settings.hudson_chat_id, e,
                )

    created_keys: list[str] = []
    if dry_run or settings.hudson_skip_jira:
        logger.info(
            "[skip] Jira PQ task creation (dry=%s skip_jira=%s)",
            dry_run, settings.hudson_skip_jira,
        )
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
