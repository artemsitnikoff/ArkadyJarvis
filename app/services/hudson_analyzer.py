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

# Проекты, попадающие в аудит часов ПОМИМО direction='WEB - ПиК'.
# key → is_internal по умолчанию (если проекта нет в dcj_projects).
# Проекты, учитываемые помимо direction='WEB - ПиК'. Значение = is_internal по умолчанию
# (0 внешний / 1 внутренний) — используется если ключа нет в dcj_projects; если есть,
# берётся флаг из dcj_projects. COZYHOME («Маркетинг») и MZNN (нет в dcj_projects) —
# внешние; DCNEW («Новый корпсайт DC», в dcj_projects под АУП, is_internal=1) — внутренний.
# Override применяется на лету при загрузке — переживает пере-импорт DCJ.xlsx.
EXTRA_AUDIT_PROJECTS: dict[str, int] = {"COZYHOME": 0, "MZNN": 0, "DCNEW": 1}

# Проекты-инстансы ПРОСТОЯ (bench). Часы по ним — это «простой», НЕ внутренняя работа.
# В DCJ.xlsx они помечены is_internal=1 → раньше ошибочно валились во внутренние часы
# (ложный 🔴 «внутр выше 8h» у людей на простое). Выносим в отдельную категорию.
# В Jira называются «Простой <роль> Digital Clouds» — детектим по имени + гарантируем
# этот набор ключей (DCBE/DCFE = BE/FE-разработчики, DCAP = аналитики, DCQQ = QA,
# DCDE = DevOps). Простой ИДЁТ в total (человек был доступен), но отдельной строкой
# и с задачей менеджеру «принять простой».
DOWNTIME_PROJECT_KEYS_FALLBACK: set[str] = {"DCBE", "DCFE", "DCAP", "DCQQ", "DCDE"}


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
    # Простой (DCBE и пр.) — отдельная категория, НЕ внутренняя работа.
    # Идёт в total_hours (человек был доступен), но не в internal/external.
    downtime_entries: list[WorklogEntry] = field(default_factory=list)
    # Часы вне набора аудита (проект не WEB-ПиК, не EXTRA_AUDIT_PROJECTS, не простой) —
    # НЕ считаются в total/internal/external, только для диагностического .md.
    out_of_scope_entries: list[WorklogEntry] = field(default_factory=list)
    total_hours: float = 0.0
    internal_hours: float = 0.0
    external_hours: float = 0.0
    downtime_hours: float = 0.0
    out_of_scope_hours: float = 0.0
    bad_comments: list[tuple[WorklogEntry, str]] = field(default_factory=list)
    absence: str | None = None       # текст «🏖 Отпуск 15.05–15.05» / None
    absence_workdays: int = 0        # сколько рабочих (Пн-Пт) дней пропустил
    weekly_norm: float = WEEKLY_HOURS_NORM  # после праздников + минус отлучки разработчика

    @property
    def on_leave(self) -> bool:
        # Полностью пропустил неделю — норма обнулилась
        return self.weekly_norm <= 0

    @property
    def is_under_norm(self) -> bool:
        if self.on_leave:
            return False
        return self.total_hours < self.weekly_norm


async def _load_web_pik_projects() -> dict[str, int]:
    """key → is_internal (0/1) для WEB-ПиК направления + EXTRA_AUDIT_PROJECTS."""
    db = get_db()
    async with db.execute(
        "SELECT project_key, is_internal FROM dcj_projects WHERE direction = ?",
        ("WEB - ПиК",),
    ) as cur:
        result = {row[0]: row[1] for row in await cur.fetchall()}
    # Доп. проекты по ключу (другое направление, но часы учитываем) — напр. COZYHOME.
    # is_internal берём из dcj_projects если есть, иначе дефолт из override.
    for key, default_internal in EXTRA_AUDIT_PROJECTS.items():
        if key in result:
            continue
        async with db.execute(
            "SELECT is_internal FROM dcj_projects WHERE project_key = ?", (key,),
        ) as cur:
            row = await cur.fetchone()
        result[key] = row[0] if row else default_internal
    return result


async def _load_downtime_keys() -> set[str]:
    """Ключи проектов-простоя: в DCJ.xlsx названы «Простой <роль> Digital Clouds».
    Берём по имени + гарантированный fallback-набор (на случай если проект не
    в dcj_projects или переименован)."""
    db = get_db()
    async with db.execute(
        "SELECT project_key FROM dcj_projects WHERE name LIKE 'Простой %'",
    ) as cur:
        keys = {row[0] for row in await cur.fetchall()}
    return keys | DOWNTIME_PROJECT_KEYS_FALLBACK


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
        return s[8:10] + "." + s[5:7]
    if len(s) >= 10 and s[2] == ".":
        return s[0:5]
    return s[:5]


def _parse_bitrix_date(raw: str) -> date | None:
    """ISO (2026-05-15...) или DMY (15.05.2026...) → date."""
    if not raw:
        return None
    raw = str(raw).strip()
    try:
        if len(raw) >= 10 and raw[4] == "-":
            return date(int(raw[:4]), int(raw[5:7]), int(raw[8:10]))
        if len(raw) >= 10 and raw[2] == ".":
            return date(int(raw[6:10]), int(raw[3:5]), int(raw[:2]))
    except ValueError:
        pass
    return None


def _count_absence_workdays(items: list[dict], since: date, until: date) -> int:
    """Сколько рабочих (Пн-Пт) дней в [since, until] перекрываются с отлучками
    разработчика. Не дублируем дни если несколько absence-записей пересекаются."""
    if not items:
        return 0
    from datetime import timedelta
    days: set[date] = set()
    for a in items:
        df = _parse_bitrix_date(a.get("DATE_ACTIVE_FROM"))
        dt = _parse_bitrix_date(a.get("DATE_ACTIVE_TO"))
        if not df or not dt:
            continue
        start = max(df, since)
        end = min(dt, until)
        d = start
        while d <= end:
            if d.weekday() < 5:
                days.add(d)
            d += timedelta(days=1)
    return len(days)


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
    downtime_keys = await _load_downtime_keys()
    devs = await _load_devs()
    if not devs:
        logger.warning("Hudson: нет разработчиков с jira_username")
        return []

    authors = [d["jira_username"] for d in devs]
    # Тянем БЕЗ фильтра по проектам — нужно увидеть и часы вне WEB-ПиК, чтобы показать
    # в .md-сверке «почему мой лог не учтён». Разбиваем на учитываемые / вне-аудита ниже.
    entries = await fetch_worklogs(authors, since, until, project_keys=None)

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
        # 3 корзины: учитываемая работа / простой (DCBE и пр.) / вне аудита.
        # Простой проверяем ПЕРВЫМ — в dcj_projects он лежит как is_internal=1 WEB-ПиК,
        # т.е. формально попал бы в `projects`, но это НЕ внутренняя работа.
        in_scope: list[WorklogEntry] = []
        downtime: list[WorklogEntry] = []
        out_scope: list[WorklogEntry] = []
        for e in dev_entries:
            if e.project_key in downtime_keys:
                downtime.append(e)
            elif e.project_key in projects:
                in_scope.append(e)
            else:
                out_scope.append(e)
        rep = DevReport(
            name=d["developer_pattern"],
            jira_username=d["jira_username"],
            manager_name=d["manager_name"],
            bitrix_id=d.get("developer_bitrix_id"),
            email=d.get("developer_email"),
            entries=in_scope,
            downtime_entries=downtime,
            out_of_scope_entries=out_scope,
            weekly_norm=week_norm,
        )
        rep.downtime_hours = sum(e.hours for e in downtime)
        rep.out_of_scope_hours = sum(e.hours for e in out_scope)
        if d.get("developer_bitrix_id") and absences:
            dev_abs = absences.get(d["developer_bitrix_id"], [])
            rep.absence = _format_absence(dev_abs)
            rep.absence_workdays = _count_absence_workdays(dev_abs, since, until)
            rep.weekly_norm = max(
                0.0, week_norm - rep.absence_workdays * HOURS_PER_DAY,
            )
        for e in in_scope:
            rep.total_hours += e.hours
            if projects.get(e.project_key) == 1:
                rep.internal_hours += e.hours
                rep.internal_entries.append(e)
            else:
                rep.external_hours += e.hours
        rep.total_hours += rep.downtime_hours  # простой = доступное время, идёт в норму
        if not skip_comment_classification:
            rep.bad_comments = await _classify_comments(
                in_scope, d["developer_pattern"], openrouter,
            )
        reports.append(rep)
    return reports
