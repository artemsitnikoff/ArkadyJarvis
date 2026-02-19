import logging
import re
from datetime import datetime

from app.services.ai_client import AIClient

logger = logging.getLogger("arkadyjarvis")

ALLOWED_TAGS = re.compile(r"</?(?:b|i|u|s|code|pre|a(?:\s[^>]*)?)>")


def clean_html_for_telegram(text: str) -> str:
    """Remove HTML tags not supported by Telegram, keep only allowed ones."""
    def _replace(m: re.Match) -> str:
        return m.group(0) if ALLOWED_TAGS.match(m.group(0)) else ""
    return re.sub(r"</?[a-zA-Z][^>]*>", _replace, text)

TASK_SUMMARY_PROMPT = """\
Проанализируй эту переписку из Telegram чата.

Сделай:
1. <b>Краткое резюме</b> — о чём шла речь (2-3 предложения)
2. <b>Задачи и ответственные</b> — кто какие задачи взял на себя или кому что поручили. \
Формат: "Имя — задача". Если задач нет — напиши "Явных задач не обнаружено."
3. <b>Ключевые решения</b> — что было решено или согласовано

Пиши на русском, кратко и по делу. Для выделения используй HTML-тег <b>...</b>, НЕ markdown.

Переписка:
"""

DAILY_OVERVIEW_PROMPT = """\
Ты получишь саммари нескольких Telegram чатов за день. \
Проанализируй их и составь ОБЩИЙ ОТЧЁТ ДНЯ.

Формат:
1. <b>🔑 Главное за день</b> — 3-5 самых важных вещей из ВСЕХ чатов. \
Каждый пункт выдели <b>жирным</b>. Это должны быть ключевые решения, критичные задачи, важные договорённости.
2. <b>📌 Все задачи</b> — сводный список задач из всех чатов: "Имя — задача (чат)". \
Если задач нет — пропусти этот блок.
3. <b>⚠️ Требует внимания</b> — что может забыться или где есть риски/дедлайны. \
Если нечего — пропусти.

Пиши на русском. Кратко, по делу. Используй HTML-теги <b>...</b> для выделения важного.

Саммари чатов:
"""


def _format_messages(msgs: list[dict]) -> str:
    return "\n".join(
        f"[{m['sent_at']}] {m.get('sender_name', m.get('sender_id', '?'))}: {m['text']}"
        for m in msgs
    )


async def summarize_messages(msgs: list[dict], max_tokens: int = 1024) -> str:
    """Run GPT summarization on a list of message dicts."""
    conversation = _format_messages(msgs)
    ai = AIClient.get()
    result = await ai.complete(TASK_SUMMARY_PROMPT + conversation, max_tokens=max_tokens)
    return clean_html_for_telegram(result)


async def summarize_from_buffer(chat_id: int, since: datetime | None = None) -> str:
    """Summarize messages from the SQLite buffer."""
    from app import db

    msgs = await db.get_buffered_messages(chat_id, since=since)
    if not msgs:
        return "Нет сообщений для суммаризации."

    logger.info(">>> SUMMARIZE: chat=%s, messages=%d", chat_id, len(msgs))
    result = await summarize_messages(msgs)
    logger.info("<<< SUMMARIZE RESPONSE:\n%s", result)
    return result


async def build_daily_overview(chat_summaries: list[tuple[str, str]]) -> str:
    parts = []
    for name, summary in chat_summaries:
        short = summary[:500] + "..." if len(summary) > 500 else summary
        parts.append(f"--- {name} ---\n{short}")

    full_text = "\n\n".join(parts)
    logger.info(">>> DAILY OVERVIEW: %d chats, input length: %d chars", len(chat_summaries), len(full_text))

    ai = AIClient.get()
    result = await ai.complete(DAILY_OVERVIEW_PROMPT + full_text, max_tokens=1500)
    result = clean_html_for_telegram(result)
    logger.info("<<< DAILY OVERVIEW RESPONSE:\n%s", result)
    return result
