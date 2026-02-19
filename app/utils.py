import re
from datetime import datetime

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
    "январь": 1, "февраль": 2, "март": 3, "апрель": 4,
    "май": 5, "июнь": 6, "июль": 7, "август": 8,
    "сентябрь": 9, "октябрь": 10, "ноябрь": 11, "декабрь": 12,
}

DAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
NICK_RE = re.compile(r"@(\w+)")


def strip_numbered_item(text: str) -> str:
    """Strip '1. ' or '2) ' prefix from text."""
    if len(text) > 2 and text[0].isdigit() and text[1] in ".)":
        return text[2:].strip()
    return text


def parse_meeting_time(text: str) -> tuple[datetime | None, str | None]:
    """Parse time and date from 'сделай встречу 1600 27 февраля'.

    Returns (datetime, error_message). If parsing fails, datetime is None.
    """
    body = re.sub(r"(?i)^(сделай|создай)\s+встречу\s*", "", text).strip()

    time_match = re.search(r"\b(\d{1,2}):(\d{2})\b", body)
    if time_match:
        hour, minute = int(time_match.group(1)), int(time_match.group(2))
    else:
        time_match = re.search(r"\b(\d{3,4})\b", body)
        if not time_match:
            return None, "Укажи время, например: сделай встречу 16:00 27 февраля"
        raw_time = time_match.group(1).zfill(4)
        hour, minute = int(raw_time[:2]), int(raw_time[2:])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, f"Некорректное время: {hour:02d}{minute:02d}"

    now = datetime.now()
    date_match = re.search(
        r"(\d{1,2})\s+(" + "|".join(MONTHS_RU.keys()) + r")",
        body.lower(),
    )
    num_date_match = re.search(r"\b(\d{1,2})\.(\d{2})\b", body) if not date_match else None
    if date_match:
        day = int(date_match.group(1))
        month = MONTHS_RU[date_match.group(2)]
    elif num_date_match:
        day = int(num_date_match.group(1))
        month = int(num_date_match.group(2))
    else:
        day = month = None

    if day is not None:
        year = now.year
        try:
            dt = datetime(year, month, day, hour, minute)
        except ValueError:
            return None, f"Некорректная дата: {day}.{month:02d}"
        if dt < now:
            dt = dt.replace(year=year + 1)
    else:
        dt = datetime(now.year, now.month, now.day, hour, minute)

    return dt, None


def parse_attendees(text: str) -> tuple[list[str], list[str]]:
    """Extract @nicknames and emails from meeting command text.

    Returns (nicknames_without_at, emails).
    """
    emails = EMAIL_RE.findall(text)
    cleaned = text
    for email in emails:
        cleaned = cleaned.replace(email, "")
    nicknames = NICK_RE.findall(cleaned)
    return nicknames, emails


def parse_bitrix_dt(s: str) -> datetime:
    """Parse Bitrix datetime string like '17.02.2026 09:00:00' or '2026-02-17T09:00:00+07:00'."""
    for fmt in ("%d.%m.%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    raise ValueError(f"Cannot parse Bitrix datetime: {s}")


def merge_intervals(intervals: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    """Merge overlapping/adjacent intervals. Returns sorted, non-overlapping list."""
    if not intervals:
        return []
    sorted_iv = sorted(intervals, key=lambda x: x[0])
    merged = [sorted_iv[0]]
    for start, end in sorted_iv[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged
