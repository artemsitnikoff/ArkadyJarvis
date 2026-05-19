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

# Минимальная норма часов в неделю — ниже → отгул-задача
WEEKLY_HOURS_NORM = 32.0
# Порог внутренних часов — выше → требует подтверждения, светится красным
INTERNAL_HOURS_WARN = 8.0


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

    @property
    def is_under_norm(self) -> bool:
        return self.total_hours < WEEKLY_HOURS_NORM


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


async def build_reports(
    since: date,
    until: date,
    openrouter: OpenRouterClient,
    skip_comment_classification: bool = False,
) -> list[DevReport]:
    """Полный сбор недельной аналитики per-dev."""
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
        )
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
