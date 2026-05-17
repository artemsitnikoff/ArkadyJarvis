"""Сбор активности менеджера по продажам из Bitrix24 за день.

Метрики (всё через `bitrix._request` REST API):
- created_leads — лиды созданные / назначенные за сутки
- activities — события CRM (звонки, встречи, задачи) выполненные сегодня
- comments — комментарии на лентах CRM
- modified_deals — сделки в которых что-то двигалось
- tasks — задачи (не-CRM)
- calls — звонки voximplant (если телефония Bitrix)
- last_login — последний вход (как proxy «был сегодня в системе»)
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

logger = logging.getLogger("arkadyjarvis")


@dataclass
class DailySalesActivity:
    user_id: int
    user_name: str = ""
    period_label: str = "сегодня"
    period_days: int = 1
    last_login: str | None = None
    leads_created: int = 0
    leads_examples: list[dict] = field(default_factory=list)
    activities_done: int = 0
    activity_types: dict[str, int] = field(default_factory=dict)
    comments_count: int = 0
    deals_modified: int = 0
    deal_examples: list[dict] = field(default_factory=list)
    tasks_done: int = 0
    calls_count: int = 0
    calls_total_seconds: int = 0
    errors: list[str] = field(default_factory=list)


def _period_bounds(tz_name: str, days: int = 1) -> tuple[str, str]:
    """ISO-строки начала и конца периода (N последних суток, включая сегодня)."""
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    end = now.replace(hour=23, minute=59, second=59, microsecond=0)
    start = (now - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return start.isoformat(), end.isoformat()


def _period_label(days: int) -> str:
    if days == 1:
        return "сегодня"
    if days == 7:
        return "за неделю"
    if days == 30:
        return "за месяц"
    return f"за {days} дн."


async def _safe_call(bitrix, method: str, params: dict, errors: list[str]):
    try:
        return await bitrix._request(method, params)
    except Exception as e:
        msg = f"{method}: {e}"
        logger.warning("Sales analytics %s", msg)
        errors.append(msg)
        return None


async def collect_user_activity(
    bitrix, user_id: int, tz_name: str = "Asia/Novosibirsk", period_days: int = 1,
) -> DailySalesActivity:
    """Собрать всю активность одного менеджера за период (по-умолчанию сегодня)."""
    day_start, day_end = _period_bounds(tz_name, period_days)
    activity = DailySalesActivity(
        user_id=user_id,
        period_days=period_days,
        period_label=_period_label(period_days),
    )

    # Имя сотрудника + последний вход
    user_resp = await _safe_call(
        bitrix, "user.get", {"ID": user_id}, activity.errors,
    )
    if user_resp and (result := (user_resp.get("result") or [])):
        u = result[0]
        activity.user_name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
        activity.last_login = u.get("LAST_LOGIN")

    # Лиды созданные / назначенные на сегодня
    leads_resp = await _safe_call(
        bitrix, "crm.lead.list",
        {
            "filter": {
                ">=DATE_CREATE": day_start,
                "<=DATE_CREATE": day_end,
                "ASSIGNED_BY_ID": user_id,
            },
            "select": ["ID", "TITLE", "STATUS_ID", "SOURCE_ID", "DATE_CREATE"],
        },
        activity.errors,
    )
    if leads_resp:
        leads = leads_resp.get("result") or []
        activity.leads_created = len(leads)
        activity.leads_examples = [
            {"id": x.get("ID"), "title": x.get("TITLE"), "status": x.get("STATUS_ID")}
            for x in leads[:5]
        ]

    # CRM-дела (звонки/встречи/задачи) — выполненные сегодня
    activities_resp = await _safe_call(
        bitrix, "crm.activity.list",
        {
            "filter": {
                ">=END_TIME": day_start,
                "<=END_TIME": day_end,
                "RESPONSIBLE_ID": user_id,
                "COMPLETED": "Y",
            },
            "select": ["ID", "TYPE_ID", "SUBJECT", "DIRECTION"],
        },
        activity.errors,
    )
    if activities_resp:
        items = activities_resp.get("result") or []
        activity.activities_done = len(items)
        type_names = {1: "Встреча", 2: "Звонок", 3: "Задача", 4: "Email"}
        for it in items:
            t = type_names.get(int(it.get("TYPE_ID", 0)), f"type{it.get('TYPE_ID')}")
            activity.activity_types[t] = activity.activity_types.get(t, 0) + 1

    # Комментарии на ленте CRM (timeline)
    comments_resp = await _safe_call(
        bitrix, "crm.timeline.comment.list",
        {
            "filter": {
                ">=CREATED": day_start,
                "<=CREATED": day_end,
                "AUTHOR_ID": user_id,
            },
            "select": ["ID"],
        },
        activity.errors,
    )
    if comments_resp:
        activity.comments_count = len(comments_resp.get("result") or [])

    # Сделки в которых что-то менялось
    deals_resp = await _safe_call(
        bitrix, "crm.deal.list",
        {
            "filter": {
                ">=DATE_MODIFY": day_start,
                "<=DATE_MODIFY": day_end,
                "ASSIGNED_BY_ID": user_id,
            },
            "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID"],
        },
        activity.errors,
    )
    if deals_resp:
        deals = deals_resp.get("result") or []
        activity.deals_modified = len(deals)
        activity.deal_examples = [
            {
                "id": d.get("ID"),
                "title": d.get("TITLE"),
                "stage": d.get("STAGE_ID"),
                "amount": d.get("OPPORTUNITY"),
                "currency": d.get("CURRENCY_ID"),
            } for d in deals[:5]
        ]

    # Задачи (общий модуль, не CRM-активности)
    tasks_resp = await _safe_call(
        bitrix, "tasks.task.list",
        {
            "filter": {
                ">=CLOSED_DATE": day_start,
                "<=CLOSED_DATE": day_end,
                "RESPONSIBLE_ID": user_id,
            },
            "select": ["ID"],
        },
        activity.errors,
    )
    if tasks_resp:
        activity.tasks_done = len((tasks_resp.get("result") or {}).get("tasks") or [])

    # Звонки voximplant — может не быть включено
    calls_resp = await _safe_call(
        bitrix, "voximplant.statistic.get",
        {
            "filter": {
                ">=CALL_START_DATE": day_start,
                "<=CALL_START_DATE": day_end,
                "PORTAL_USER_ID": user_id,
            },
        },
        activity.errors,
    )
    if calls_resp:
        calls = calls_resp.get("result") or []
        activity.calls_count = len(calls)
        activity.calls_total_seconds = sum(int(c.get("CALL_DURATION") or 0) for c in calls)

    return activity


async def collect_for_user_ids(
    bitrix, user_ids: list[int], tz_name: str = "Asia/Novosibirsk", period_days: int = 1,
) -> list[DailySalesActivity]:
    return await asyncio.gather(
        *[collect_user_activity(bitrix, uid, tz_name, period_days) for uid in user_ids]
    )
