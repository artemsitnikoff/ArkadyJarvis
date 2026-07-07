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
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


@dataclass
class CallInfo:
    call_id: str
    direction: str         # "in" / "out" / "missed" / "callback" / "other"
    direction_label: str   # "Входящий" / "Исходящий" / "Пропущенный" / "Обратный"
    phone: str
    duration_sec: int
    entity_type: str | None = None        # "LEAD" / "CONTACT" / "COMPANY" / "DEAL"
    entity_id: int | None = None
    entity_name: str | None = None
    start_time: str | None = None
    summary: str | None = None            # AI-разбор (Суть / Хорошо / Улучшить)
    transcript: str | None = None         # полный диаризованный текст (S1 [0:00]: ...)
    has_record: bool = False              # есть ли запись (URL/file)


CALL_DIRECTIONS = {
    1: ("out", "Исходящий"),
    2: ("in", "Входящий"),
    3: ("missed", "Пропущенный"),
    4: ("callback", "Обратный"),
}


@dataclass
class DailySalesActivity:
    user_id: int
    user_name: str = ""
    period_label: str = "сегодня"
    period_days: int = 1
    last_login: str | None = None

    # Лиды
    leads_created: int = 0
    leads_active: int = 0
    leads_examples: list[dict] = field(default_factory=list)

    # Сделки
    deals_created: int = 0
    deals_active: int = 0
    deals_modified: int = 0
    deals_won: int = 0
    deals_won_sum: float = 0.0
    deals_hot: int = 0
    deals_hot_sum: float = 0.0
    avg_deal_age_days: float = 0.0
    deal_examples: list[dict] = field(default_factory=list)

    # План/факт за календарный месяц
    month_won_sum: float = 0.0
    month_won_count: int = 0
    monthly_plan: float = 0.0

    # Дела/активность
    activities_done: int = 0
    activity_types: dict[str, int] = field(default_factory=dict)
    stage_changes: int = 0  # переходы сделок по этапам
    comments_count: int = 0

    # Звонки
    calls_count: int = 0
    calls_total_seconds: int = 0
    calls_by_direction: dict[str, int] = field(default_factory=dict)
    calls: list[CallInfo] = field(default_factory=list)

    errors: list[str] = field(default_factory=list)


def _period_bounds(
    tz_name: str, days: int = 1, as_of: "date | None" = None,
) -> tuple[str, str]:
    """ISO-строки начала и конца периода (N последних суток, включая as_of).
    Если as_of=None — берётся 'сегодня'. Иначе период заканчивается as_of."""
    from datetime import date as _date  # noqa: F401
    tz = ZoneInfo(tz_name)
    if as_of:
        end = datetime(
            as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=tz,
        )
    else:
        end = datetime.now(tz).replace(hour=23, minute=59, second=59, microsecond=0)
    start = (end - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )
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


async def _list_all(
    bitrix, method: str, params: dict, errors: list[str], *, max_pages: int = 40,
) -> list[dict]:
    """Все страницы list-метода Bitrix. `*.list` отдаёт максимум 50 строк за раз —
    без этого любой `len(result)` молча упирался в 50 (баг «всегда 50 в работе»).
    Идём по `resp['next']` (offset следующей страницы). max_pages — защита от
    бесконечного цикла (40*50 = 2000 строк)."""
    items: list[dict] = []
    start = 0
    for _ in range(max_pages):
        p = dict(params)
        if start:
            p["start"] = start
        resp = await _safe_call(bitrix, method, p, errors)
        if not resp:
            break
        batch = resp.get("result") or []
        items.extend(batch)
        nxt = resp.get("next")
        if not nxt or not batch:
            break
        start = nxt
    else:
        logger.warning(
            "Sales analytics: %s достиг max_pages=%d — данные могут быть обрезаны",
            method, max_pages,
        )
    return items


async def _list_total(bitrix, method: str, params: dict, errors: list[str]) -> int:
    """Точное число записей через поле `total` первой страницы (list-методы Bitrix
    его всегда возвращают). Одним запросом, без вытягивания всех строк — для
    метрик, где нужно только количество."""
    resp = await _safe_call(bitrix, method, params, errors)
    if not resp:
        return 0
    total = resp.get("total")
    return int(total) if total is not None else len(resp.get("result") or [])


async def collect_user_activity(
    bitrix,
    user_id: int,
    tz_name: str = "Asia/Novosibirsk",
    period_days: int = 1,
    openrouter=None,
    ai_client=None,
    with_transcripts: bool = False,
    as_of: "date | None" = None,
) -> DailySalesActivity:
    """Собрать активность менеджера за период. Если `with_transcripts=True` и
    переданы `openrouter` + `ai_client` — для каждого звонка с записью
    скачивает MP3, транскрибирует и формирует AI-выжимку.

    `as_of` — конкретная дата конца периода (для backfill). None = сейчас.
    """
    day_start, day_end = _period_bounds(tz_name, period_days, as_of=as_of)
    activity = DailySalesActivity(
        user_id=user_id,
        period_days=period_days,
        period_label=_period_label(period_days),
    )

    # Разрешённые воронки сделок (default 27,31,33 — Услуги Б24 / Общая / ПиК).
    # Исключает «Счета 1С» (cat 0 — дубли-фантомы автодвижений 1С), «Продление
    # Битрикс» (29), «Квал» (23). Применяется КО ВСЕМ метрикам сделок: активные,
    # модифицированные, WON, план/факт и переходы по этапам — чтобы движения
    # счетов не попадали ни в цифры сделок, ни в «действия».
    allowed_cats = {
        int(c.strip()) for c in settings.sales_report_deal_categories.split(",")
        if c.strip().isdigit()
    }
    cat_filter = {"CATEGORY_ID": list(allowed_cats)} if allowed_cats else {}

    # Имя сотрудника + последний вход
    user_resp = await _safe_call(
        bitrix, "user.get", {"ID": user_id}, activity.errors,
    )
    if user_resp and (result := (user_resp.get("result") or [])):
        u = result[0]
        activity.user_name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
        activity.last_login = u.get("LAST_LOGIN")

    # Лиды созданные / назначенные на сегодня
    leads = await _list_all(
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
    activity.leads_created = len(leads)
    activity.leads_examples = [
        {"id": x.get("ID"), "title": x.get("TITLE"), "status": x.get("STATUS_ID")}
        for x in leads[:5]
    ]

    # CRM-дела (звонки/встречи/задачи) — выполненные сегодня
    items = await _list_all(
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
    activity.activities_done = len(items)
    type_names = {1: "Встреча", 2: "Звонок", 3: "Задача", 4: "Email"}
    for it in items:
        t = type_names.get(int(it.get("TYPE_ID", 0)), f"type{it.get('TYPE_ID')}")
        activity.activity_types[t] = activity.activity_types.get(t, 0) + 1

    # Комментарии на ленте CRM (timeline)
    activity.comments_count = await _list_total(
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

    # Сделки модифицированы за период (только разрешённые воронки — без «Счетов 1С»)
    modified_deals = await _list_all(
        bitrix, "crm.deal.list",
        {
            "filter": {
                ">=DATE_MODIFY": day_start,
                "<=DATE_MODIFY": day_end,
                "ASSIGNED_BY_ID": user_id,
                **cat_filter,
            },
            "select": ["ID", "TITLE", "STAGE_ID", "OPPORTUNITY", "CURRENCY_ID",
                       "DATE_CREATE", "CLOSED", "CLOSEDATE"],
        },
        activity.errors,
    )
    if modified_deals:
        activity.deals_modified = len(modified_deals)
        # Созданные за период
        activity.deals_created = sum(
            1 for d in modified_deals if (d.get("DATE_CREATE") or "") >= day_start
        )
        # WON за период
        won = [d for d in modified_deals
               if str(d.get("STAGE_ID") or "").endswith("WON") and d.get("CLOSED") == "Y"]
        activity.deals_won = len(won)
        activity.deals_won_sum = sum(float(d.get("OPPORTUNITY") or 0) for d in won)
        activity.deal_examples = [
            {
                "id": d.get("ID"),
                "title": d.get("TITLE"),
                "stage": d.get("STAGE_ID"),
                "amount": d.get("OPPORTUNITY"),
                "currency": d.get("CURRENCY_ID"),
            } for d in modified_deals[:5]
        ]

    # Все открытые (активные) сделки менеджера в разрешённых воронках (без «Счетов
    # 1С») — для счёта горячих / среднего возраста. Пагинация обязательна: без неё
    # «в работе» упиралось в 50.
    in_cat = await _list_all(
        bitrix, "crm.deal.list",
        {
            "filter": {"ASSIGNED_BY_ID": user_id, "CLOSED": "N", **cat_filter},
            "select": ["ID", "TITLE", "STAGE_ID", "CATEGORY_ID", "OPPORTUNITY", "DATE_CREATE"],
        },
        activity.errors,
    )
    active_deals: list[dict] = in_cat
    if in_cat:
        # «В работе» = все сделки в разрешённых воронках (без фильтра стадий)
        activity.deals_active = len(active_deals)

        # 2) Фетчим имена стадий для этих воронок — нужно для «горячих»
        stage_name_by_id: dict[str, str] = {}
        cats_to_load = {int(d.get("CATEGORY_ID") or 0) for d in in_cat}
        for cat_id in cats_to_load:
            if cat_id == 0:
                continue
            sr = await _safe_call(
                bitrix, "crm.dealcategory.stage.list", {"id": cat_id}, activity.errors,
            )
            for s in (sr.get("result") or [] if sr else []):
                stage_name_by_id[s.get("STATUS_ID")] = (s.get("NAME") or "").lower()

        # «Горячие» = подмножество в работе, стадия по NAME подходит под паттерн
        # (от КП и далее: КП / договор / счёт / переговоры / КЭВ проведён / отработка...)
        hot_patterns = [
            p.strip().lower()
            for p in settings.sales_report_active_deal_patterns.split(",")
            if p.strip()
        ]

        def _is_hot_stage(stage_id: str | None) -> bool:
            name = stage_name_by_id.get(stage_id or "", "")
            return any(p in name for p in hot_patterns)

        hot = [d for d in in_cat if _is_hot_stage(d.get("STAGE_ID"))]
        activity.deals_hot = len(hot)
        activity.deals_hot_sum = sum(float(d.get("OPPORTUNITY") or 0) for d in hot)

        # Средний возраст всех активных сделок (в днях)
        from datetime import datetime as _dt
        ages = []
        now_dt = _dt.now(ZoneInfo(tz_name))
        for d in active_deals:
            ds = d.get("DATE_CREATE")
            if not ds:
                continue
            try:
                dt = _dt.fromisoformat(ds.replace("Z", "+00:00"))
                ages.append((now_dt - dt).days)
            except Exception:
                pass
        activity.avg_deal_age_days = round(sum(ages) / len(ages), 1) if ages else 0.0

    # Активные лиды — все где статус НЕ "успех" (S=CONVERTED) и НЕ "отказ" (F=JUNK/Дубль/Дорого/...)
    # Bitrix хранит семантику статуса в STATUS_SEMANTIC_ID: NULL=в работе, S=success, F=fail.
    activity.leads_active = await _list_total(
        bitrix, "crm.lead.list",
        {
            "filter": {
                "ASSIGNED_BY_ID": user_id,
                "!STATUS_SEMANTIC_ID": ["S", "F"],
            },
            "select": ["ID", "STATUS_ID"],
        },
        activity.errors,
    )

    # План/факт — WON за календарный месяц
    tz = ZoneInfo(tz_name)
    now = datetime.now(tz)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    # WON только в разрешённых воронках — «Счета 1С» (cat 0) в план/факт НЕ идут.
    won_month = await _list_all(
        bitrix, "crm.deal.list",
        {
            "filter": {
                "ASSIGNED_BY_ID": user_id,
                ">=CLOSEDATE": month_start,
                "STAGE_SEMANTIC_ID": "S",   # successful (WON)
                **cat_filter,
            },
            "select": ["ID", "OPPORTUNITY"],
        },
        activity.errors,
    )
    activity.month_won_count = len(won_month)
    activity.month_won_sum = sum(float(d.get("OPPORTUNITY") or 0) for d in won_month)
    activity.monthly_plan = float(settings.sales_report_monthly_plan)

    # Переходы сделок по этапам за период — crm.stagehistory.list по каждой сделке менеджера
    deal_ids = set()
    for d in modified_deals:
        if d.get("ID"):
            deal_ids.add(str(d["ID"]))
    for d in active_deals:
        if d.get("ID"):
            deal_ids.add(str(d["ID"]))
    if deal_ids:
        activity.stage_changes = await _count_stage_changes(
            bitrix, list(deal_ids), day_start, day_end,
        )

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
        calls_raw = calls_resp.get("result") or []
        activity.calls_count = len(calls_raw)
        activity.calls_total_seconds = sum(int(c.get("CALL_DURATION") or 0) for c in calls_raw)
        activity.calls = await _enrich_calls(
            bitrix, calls_raw, openrouter=openrouter, ai_client=ai_client,
            transcribe=with_transcripts, errors=activity.errors,
        )
        for c in activity.calls:
            activity.calls_by_direction[c.direction_label] = (
                activity.calls_by_direction.get(c.direction_label, 0) + 1
            )

    return activity


async def collect_for_user_ids(
    bitrix,
    user_ids: list[int],
    tz_name: str = "Asia/Novosibirsk",
    period_days: int = 1,
    openrouter=None,
    ai_client=None,
    with_transcripts: bool = False,
    as_of: "date | None" = None,
) -> list[DailySalesActivity]:
    return await asyncio.gather(
        *[
            collect_user_activity(
                bitrix, uid, tz_name, period_days,
                openrouter=openrouter, ai_client=ai_client,
                with_transcripts=with_transcripts, as_of=as_of,
            )
            for uid in user_ids
        ]
    )


# ── enrichment helpers ─────────────────────────────────────────────────

ENTITY_GETTER = {
    "LEAD": ("crm.lead.get", "TITLE"),
    "CONTACT": ("crm.contact.get", None),  # NAME + LAST_NAME
    "COMPANY": ("crm.company.get", "TITLE"),
    "DEAL": ("crm.deal.get", "TITLE"),
}


async def _resolve_entity_name(bitrix, entity_type: str, entity_id: int) -> str | None:
    spec = ENTITY_GETTER.get(entity_type)
    if not spec:
        return None
    method, field_name = spec
    try:
        r = await bitrix._request(method, {"id": entity_id})
        e = r.get("result") or {}
        if field_name:
            return e.get(field_name)
        # CONTACT — собираем имя
        parts = [e.get("LAST_NAME"), e.get("NAME"), e.get("SECOND_NAME")]
        name = " ".join(p for p in parts if p).strip()
        return name or None
    except Exception as e:
        logger.debug("Resolve %s/%s failed: %s", entity_type, entity_id, e)
        return None


async def _enrich_calls(
    bitrix, calls_raw: list[dict],
    openrouter=None, ai_client=None, transcribe: bool = False,
    errors: list[str] | None = None,
    max_transcripts: int = 25,
) -> list[CallInfo]:
    # Сначала формируем базовые CallInfo
    entity_cache: dict[tuple[str, int], str | None] = {}
    base: list[CallInfo] = []
    for c in calls_raw:
        ct = int(c.get("CALL_TYPE") or 0)
        dir_code, dir_label = CALL_DIRECTIONS.get(ct, ("other", f"type{ct}"))
        e_type = c.get("CRM_ENTITY_TYPE")
        e_id = c.get("CRM_ENTITY_ID")
        ci = CallInfo(
            call_id=str(c.get("ID") or c.get("CALL_ID") or ""),
            direction=dir_code,
            direction_label=dir_label,
            phone=str(c.get("PHONE_NUMBER") or ""),
            duration_sec=int(c.get("CALL_DURATION") or 0),
            entity_type=e_type,
            entity_id=int(e_id) if e_id else None,
            start_time=c.get("CALL_START_DATE"),
            has_record=bool(c.get("RECORD_FILE_ID") or c.get("CALL_RECORD_URL")),
        )
        base.append(ci)

    # Резолвим имена связанных сущностей (с кэшем чтобы не дёргать одно и то же)
    for ci in base:
        if not (ci.entity_type and ci.entity_id):
            continue
        key = (ci.entity_type, ci.entity_id)
        if key not in entity_cache:
            entity_cache[key] = await _resolve_entity_name(bitrix, ci.entity_type, ci.entity_id)
        ci.entity_name = entity_cache[key]

    if not transcribe or not openrouter or not ai_client:
        return base

    # Параллельная транскрипция: только звонки >=15 секунд и с записью
    candidates = [(i, c, calls_raw[i]) for i, c in enumerate(base)
                  if c.has_record and c.duration_sec >= 15]
    if not candidates:
        return base

    # Если звонков много (>max_transcripts) — берём самые длинные
    if len(candidates) > max_transcripts:
        candidates.sort(key=lambda x: x[1].duration_sec, reverse=True)
        candidates = candidates[:max_transcripts]
        logger.info(
            "Sales analytics: transcribing top %d of %d eligible calls (longest first)",
            max_transcripts, len(base),
        )

    sem = asyncio.Semaphore(3)  # ограничиваем нагрузку на OpenRouter / Bitrix

    async def _do(idx: int, ci: CallInfo, raw: dict):
        async with sem:
            try:
                summary, transcript = await _transcribe_and_summarize_call(
                    bitrix, openrouter, ai_client, raw,
                )
                if summary:
                    ci.summary = summary
                if transcript:
                    ci.transcript = transcript
            except Exception as e:
                msg = f"transcribe call {ci.call_id}: {e}"
                logger.warning("Sales analytics %s", msg)
                if errors is not None:
                    errors.append(msg)

    await asyncio.gather(*[_do(i, c, r) for i, c, r in candidates])
    return base


async def _transcribe_and_summarize_call(
    bitrix, openrouter, ai_client, call_raw: dict,
) -> tuple[str | None, str | None]:
    """Скачивает запись, транскрибирует, делает структурный разбор.
    Возвращает (summary, transcript) — оба могут быть None."""
    import os
    import tempfile

    import httpx

    from app.services.prompts import load_prompt

    file_id = call_raw.get("RECORD_FILE_ID")
    download_url: str | None = None
    if file_id:
        try:
            r = await bitrix._request("disk.file.get", {"id": file_id})
            download_url = ((r.get("result") or {}).get("DOWNLOAD_URL"))
        except Exception as e:
            logger.debug("disk.file.get for %s failed: %s", file_id, e)
    if not download_url:
        download_url = call_raw.get("CALL_RECORD_URL")
    if not download_url:
        return None, None

    fd, tmp_path = tempfile.mkstemp(suffix=".mp3", prefix="call_")
    os.close(fd)
    try:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
            resp = await http.get(download_url)
            if resp.status_code != 200 or not resp.content:
                return None, None
            with open(tmp_path, "wb") as f:
                f.write(resp.content)

        tr = await openrouter.transcribe_voice(tmp_path, audio_format="mp3")
        if not tr.success or not tr.full_text:
            return None, None
        transcript = tr.full_text

        dc_context = load_prompt("digital_clouds_context")
        analysis_prompt = load_prompt("sales_call_analysis")
        prompt = (
            analysis_prompt
            .replace("{dc_context}", dc_context)
            .replace("{transcript}", transcript[:6000])
        )
        summary_raw = await ai_client.complete(prompt, timeout=90)
        summary = summary_raw.strip() if summary_raw else None
        return summary, transcript
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass


def _fmt_duration(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}"


def build_transcripts_bundle(activities: list[DailySalesActivity]) -> str:
    """Собирает все транскрипты звонков из списка активностей в один markdown.
    Возвращает текст файла (или пустую строку если транскриптов нет)."""
    has_any = any(c.transcript for a in activities for c in a.calls)
    if not has_any:
        return ""

    out: list[str] = ["# Расшифровки звонков", ""]
    for a in activities:
        calls_with_text = [c for c in a.calls if c.transcript]
        if not calls_with_text:
            continue
        out.append(f"## {a.user_name or f'User #{a.user_id}'} · {a.period_label}")
        out.append("")
        for i, c in enumerate(calls_with_text, 1):
            entity_part = f"  · {c.entity_type or ''}: «{c.entity_name}»" if c.entity_name else ""
            out.append(f"### {i}. {c.direction_label} · {c.phone} · {_fmt_duration(c.duration_sec)}{entity_part}")
            if c.start_time:
                out.append(f"_{c.start_time}_")
            out.append("")
            if c.summary:
                out.append("**Разбор:**")
                out.append("")
                out.append(c.summary)
                out.append("")
            out.append("**Расшифровка:**")
            out.append("")
            out.append("```")
            out.append(c.transcript)
            out.append("```")
            out.append("")
            out.append("---")
            out.append("")
    return "\n".join(out)


async def _count_stage_changes(
    bitrix, deal_ids: list[str], day_start: str, day_end: str,
) -> int:
    """Считает сколько переходов по этапам сделок было в период.

    `crm.stagehistory.list` у Bitrix не умеет фильтровать по менеджеру —
    только по OWNER_ID (это ID сделки). Поэтому N+1: для каждой сделки
    отдельный запрос. Параллелим (5 одновременно), ошибки тихо игнорим.
    """
    if not deal_ids:
        return 0
    sem = asyncio.Semaphore(5)

    async def _one(deal_id: str) -> int:
        async with sem:
            try:
                r = await bitrix._request(
                    "crm.stagehistory.list",
                    {
                        "entityTypeId": 2,  # DEAL
                        "filter": {
                            "OWNER_ID": deal_id,
                            ">=CREATED_TIME": day_start,
                            "<=CREATED_TIME": day_end,
                        },
                    },
                )
                items = r.get("result") or {}
                if isinstance(items, dict):
                    items = items.get("items") or []
                return len(items) if isinstance(items, list) else 0
            except Exception as e:
                logger.debug("stagehistory deal=%s: %s", deal_id, e)
                return 0

    counts = await asyncio.gather(*[_one(d) for d in deal_ids])
    total = sum(counts)
    logger.info(
        "stagehistory: %d transitions across %d deals (%s..%s)",
        total, len(deal_ids), day_start[:10], day_end[:10],
    )
    return total
