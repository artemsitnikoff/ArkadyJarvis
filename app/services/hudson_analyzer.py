"""Мисис Хадсон — анализатор недельных worklog'ов разработчиков WEB-ПиК.

Шаги:
1. Загружает разработчиков из hudson_managers (с jira_username)
2. Загружает WEB-ПиК проекты из dcj_projects (с флагом is_internal)
3. Тянет Jira worklog'и за период по этим авторам/проектам
4. Per-dev: суммирует часы, разбивает на internal/external
5. Per-comment: Haiku-классификатор качества комментария (parallel sem(5))
6. Возвращает структуру для дальнейшей рассылки менеджерам / постановки задач

Эта стадия — только аналитика. Постановка Jira-задач, отправка в Telegram —
в `scheduler.jobs.hudson_weekly_job`.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date

from app.db import get_db
from app.services.jira_worklog import WorklogEntry, fetch_worklogs
from app.services.openrouter_client import OpenRouterClient
from app.services.prompts import load_prompt
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvis")

# Базовая норма часов в неделю (Пн-Пт минус 1 час за пятницу — общепринятая в DC)
WEEKLY_HOURS_NORM = 32.0
HOURS_PER_DAY = 8.0
# Порог внутренних часов — выше → требует подтверждения, светится красным
INTERNAL_HOURS_WARN = 8.0


async def compute_weekly_norm(since: date, until: date) -> float:
    """Норма часов с учётом ТК РФ — берём производственный календарь из
    isdayoff.ru и вычитаем 8h за каждый праздничный день, попавший на Пн-Пт.
    Не меньше 0."""
    from app.services.holidays_api import count_holidays_in_workweek
    holidays = await count_holidays_in_workweek(since, until)
    return max(0.0, WEEKLY_HOURS_NORM - holidays * HOURS_PER_DAY)


@dataclass
class DevReport:
    name: str
    jira_username: str
    manager_name: str
    bitrix_id: int | None
    email: str | None
    entries: list[WorklogEntry] = field(default_factory=list)
    internal_entries: list[WorklogEntry] = field(default_factory=list)
    total_hours: float = 0.0
    internal_hours: float = 0.0
    external_hours: float = 0.0
    bad_comments: list[tuple[WorklogEntry, str]] = field(default_factory=list)
    absence: str | None = None  # «🏖 Отпуск 15.05–22.05» / None если нет
    weekly_norm: float = WEEKLY_HOURS_NORM  # с учётом праздников этой недели

    @property
    def on_leave(self) -> bool:
        return bool(self.absence)

    @property
    def is_under_norm(self) -> bool:
        # На отпускников не вешаем недоборы — норма часов не действует
        if self.on_leave:
            return False
        return self.total_hours < self.weekly_norm


async def _load_web_pik_projects() -> dict[str, int]:
    """key → is_internal (0/1) для WEB-ПиК направления."""
    db = get_db()
    async with db.execute(
        "SELECT project_key, is_internal FROM dcj_projects WHERE direction = ?",
        ("WEB - ПиК",),
    ) as cur:
        return {row[0]: row[1] for row in await cur.fetchall()}


async def _load_devs() -> list[dict]:
    """Разработчики с привязкой к менеджеру + jira_username."""
    db = get_db()
    async with db.execute(
        "SELECT developer_pattern, jira_username, manager_name, "
        "developer_bitrix_id, developer_email "
        "FROM hudson_managers WHERE jira_username IS NOT NULL",
    ) as cur:
        return [dict(row) for row in await cur.fetchall()]


async def _classify_comments(
    entries: list[WorklogEntry],
    developer_name: str,
    openrouter: OpenRouterClient,
) -> list[tuple[WorklogEntry, str]]:
    """Параллельная (sem=5) Haiku-классификация через OpenRouter (anthropic/claude-haiku-4.5).
    Возвращает список плохих коммов."""
    if not entries:
        return []
    prompt_tmpl = load_prompt("hudson_bad_comment")
    sem = asyncio.Semaphore(5)
    bad: list[tuple[WorklogEntry, str]] = []

    async def check(entry: WorklogEntry) -> None:
        async with sem:
            prompt = (
                prompt_tmpl.replace("{developer}", developer_name)
                .replace("{issue_key}", entry.issue_key)
                .replace("{issue_summary}", entry.issue_summary or "—")
                .replace("{hours}", f"{entry.hours:.2f}")
                .replace("{comment}", entry.comment or "(пусто)")
            )
            last_err: Exception | None = None
            for attempt in (1, 2):
                try:
                    resp = await openrouter.complete_text(
                        prompt,
                        model="anthropic/claude-haiku-4.5",
                        json_mode=True,
                        timeout=60.0,
                    )
                    data = parse_json_response(resp) or {}
                    if data.get("is_bad"):
                        bad.append((entry, str(data.get("reason", "")).strip()))
                    return
                except Exception as e:
                    last_err = e
                    if attempt == 1:
                        await asyncio.sleep(2)
            logger.warning(
                "Hudson bad-comment classify failed for %s after 2 attempts: %s",
                entry.issue_key, last_err,
            )

    await asyncio.gather(*(check(e) for e in entries))
    return bad


def _format_absence(items: list[dict]) -> str | None:
    """Текстовое описание самой релевантной отлучки (отпуск > командировка > болезнь).
    items — отдача absence.list ИЛИ кальки из calendar.event.get fallback."""
    if not items:
        return None
    type_label = {"V": "🏖 Отпуск", "B": "✈ Командировка", "A": "🤒 Болеет"}
    priority = {"V": 0, "B": 1, "A": 2}
    items_sorted = sorted(
        items, key=lambda r: priority.get((r.get("TYPE") or "").upper(), 9),
    )
    a = items_sorted[0]
    label = type_label.get((a.get("TYPE") or "").upper(), "Отсутствует")
    df = _short_date(a.get("DATE_ACTIVE_FROM") or "")
    dt = _short_date(a.get("DATE_ACTIVE_TO") or "")
    if df and dt:
        return f"{label} {df}–{dt}"
    return label


def _short_date(s: str) -> str:
    """Из ISO (`2026-05-15T…`) или DMY (`15.05.2026 00:00:00`) → `15.05`."""
    if not s:
        return ""
    if len(s) >= 10 and s[4] == "-":
        # YYYY-MM-DD…
        return s[8:10] + "." + s[5:7]
    if len(s) >= 10 and s[2] == ".":
        # DD.MM.YYYY…
        return s[0:5]
    return s[:5]


async def build_reports(
    since: date,
    until: date,
    openrouter: OpenRouterClient,
    bitrix=None,
    skip_comment_classification: bool = False,
) -> list[DevReport]:
    """Полный сбор недельной аналитики per-dev. `bitrix` — опционален, нужен для
    подгрузки отпусков/больничных."""
    projects = await _load_web_pik_projects()
    if not projects:
        logger.warning("Hudson: dcj_projects не содержит WEB-ПиК проектов")
        return []
    devs = await _load_devs()
    if not devs:
        logger.warning("Hudson: нет разработчиков с jira_username")
        return []

    authors = [d["jira_username"] for d in devs]
    entries = await fetch_worklogs(
        authors, since, until, project_keys=set(projects.keys()),
    )

    # group by author
    by_author: dict[str, list[WorklogEntry]] = {}
    for e in entries:
        by_author.setdefault(e.author, []).append(e)

    # absences (опционально)
    absences: dict[int, list[dict]] = {}
    if bitrix is not None:
        bx_ids = [d["developer_bitrix_id"] for d in devs if d.get("developer_bitrix_id")]
        try:
            absences = await bitrix.get_absences(
                bx_ids, since.isoformat(), until.isoformat(),
            )
        except Exception as e:
            logger.warning("Hudson: не смог получить absences из Bitrix: %s", e)

    week_norm = await compute_weekly_norm(since, until)
    if week_norm != WEEKLY_HOURS_NORM:
        logger.info("Hudson: норма недели = %.0fh (учли производственный календарь)", week_norm)

    reports: list[DevReport] = []
    for d in devs:
        dev_entries = by_author.get(d["jira_username"], [])
        rep = DevReport(
            name=d["developer_pattern"],
            jira_username=d["jira_username"],
            manager_name=d["manager_name"],
            bitrix_id=d.get("developer_bitrix_id"),
            email=d.get("developer_email"),
            entries=dev_entries,
            weekly_norm=week_norm,
        )
        if d.get("developer_bitrix_id") and absences:
            rep.absence = _format_absence(absences.get(d["developer_bitrix_id"], []))
        for e in dev_entries:
            rep.total_hours += e.hours
            if projects.get(e.project_key) == 1:
                rep.internal_hours += e.hours
                rep.internal_entries.append(e)
            else:
                rep.external_hours += e.hours
        if not skip_comment_classification:
            rep.bad_comments = await _classify_comments(
                dev_entries, d["developer_pattern"], openrouter,
            )
        reports.append(rep)
    return reports
