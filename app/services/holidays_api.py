"""Производственный календарь РФ через isdayoff.ru — бесплатное API без авторизации.

GET https://isdayoff.ru/YYYYMMDD → один символ:
  0 = рабочий день
  1 = нерабочий (выходной или гос. праздник)
  2 = сокращённый рабочий день (предпраздничный)
  4 = нет данных / ошибка

Кэшируем результаты в process-level dict.
"""
import logging
from datetime import date, timedelta

import httpx

logger = logging.getLogger("arkadyjarvis")

_cache: dict[date, str] = {}
_BASE = "https://isdayoff.ru"


async def is_non_working(d: date) -> bool:
    """True если дата нерабочая (выходной/праздник). False — рабочий день
    или ошибка API (на safe side)."""
    if d in _cache:
        return _cache[d] == "1"
    url = f"{_BASE}/{d.strftime('%Y%m%d')}"
    try:
        async with httpx.AsyncClient(timeout=10.0) as http:
            r = await http.get(url)
        code = r.text.strip() if r.status_code == 200 else "4"
    except Exception as e:
        logger.warning("isdayoff.ru %s failed: %s", d, e)
        code = "4"
    _cache[d] = code
    return code == "1"


async def count_holidays_in_workweek(since: date, until: date) -> int:
    """Сколько рабочих дней (Пн-Пт) в [since, until] помечены как нерабочие
    (т.е. праздники, попавшие на будни)."""
    cnt = 0
    d = since
    while d <= until:
        if d.weekday() < 5 and await is_non_working(d):
            cnt += 1
        d += timedelta(days=1)
    return cnt
