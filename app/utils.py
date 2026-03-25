import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings

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

    now = datetime.now(ZoneInfo(settings.timezone)).replace(tzinfo=None)
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


def md_to_telegram_html(text: str) -> str:
    """Convert Markdown to Telegram-compatible HTML."""
    import html as html_mod

    lines = text.split("\n")
    result: list[str] = []
    in_code_block = False
    code_lines: list[str] = []

    for line in lines:
        # Code block toggle
        if line.strip().startswith("```"):
            if in_code_block:
                result.append("<pre>" + html_mod.escape("\n".join(code_lines)) + "</pre>")
                code_lines = []
                in_code_block = False
            else:
                in_code_block = True
            continue

        if in_code_block:
            code_lines.append(line)
            continue

        # Headers → bold
        header = re.match(r"^(#{1,6})\s+(.*)", line)
        if header:
            result.append(f"\n<b>{html_mod.escape(header.group(2))}</b>\n")
            continue

        # Escape HTML in normal lines first, then apply formatting
        escaped = html_mod.escape(line)

        # Inline code (before bold/italic to avoid conflicts)
        escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)

        # Bold **text** or __text__
        escaped = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)
        escaped = re.sub(r"__(.+?)__", r"<b>\1</b>", escaped)

        # Italic *text* or _text_ (but not inside words like some_var_name)
        escaped = re.sub(r"(?<!\w)\*([^*]+?)\*(?!\w)", r"<i>\1</i>", escaped)
        escaped = re.sub(r"(?<!\w)_([^_]+?)_(?!\w)", r"<i>\1</i>", escaped)

        # List markers
        escaped = re.sub(r"^(\s*)[-*]\s", r"\1• ", escaped)

        result.append(escaped)

    # Close unclosed code block
    if in_code_block and code_lines:
        result.append("<pre>" + html_mod.escape("\n".join(code_lines)) + "</pre>")

    return "\n".join(result).strip()


def parse_json_response(raw: str) -> dict:
    """Extract JSON object from AI response — handles markdown fences and embedded text."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end > start:
        return json.loads(raw[start:end + 1])
    raise ValueError(f"Cannot parse JSON from AI response: {raw[:200]}")


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
