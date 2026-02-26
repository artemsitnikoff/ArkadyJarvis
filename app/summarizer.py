from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.ai_client import AIClient

logger = logging.getLogger("arkadyjarvis")

TASK_SUMMARY_PROMPT = """\
Проанализируй эту переписку из Telegram чата.

Сделай:
1. <b>Краткое резюме</b> — о чём шла речь (2-3 предложения)
2. <b>Задачи и ответственные</b> — кто какие задачи взял на себя или кому что поручили. \
Формат: "Имя — задача". Если задач нет — напиши "Явных задач не обнаружено."
3. <b>Ключевые решения</b> — что было решено или согласовано
4. <b>❓ Без ответа</b> — вопросы или просьбы, на которые НЕ последовал ответ в переписке. \
Особенно если человека упомянули (@имя). Формат: "Кому → вопрос/просьба (от Кого)". \
Если всё отвечено — пропусти этот блок.

Пиши на русском, кратко и по делу.
Используй ТОЛЬКО теги: <b>, <i>, <code>. НЕ используй markdown, <br>, <p>, <div> и другие HTML-теги.

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
4. <b>❓ Без ответа</b> — неотвеченные вопросы/просьбы из всех чатов. \
Особенно с упоминанием (@имя). Формат: "Кому → что (чат, от Кого)". \
Если всё отвечено — пропусти.

Пиши на русском. Кратко, по делу.
Используй ТОЛЬКО теги: <b>, <i>, <code>. НЕ используй markdown, <br>, <p>, <div> и другие HTML-теги.

Саммари чатов:
"""


def _format_messages(msgs: list[dict]) -> str:
    return "\n".join(
        f"[{m['sent_at']}] {m.get('sender_name', m.get('sender_id', '?'))}: {m['text']}"
        for m in msgs
    )


MAX_INPUT_CHARS = 100_000  # ~25K tokens, safe for GPT context window


async def summarize_messages(
    msgs: list[dict], *, ai_client: AIClient, max_tokens: int = 1024,
) -> str:
    """Run GPT summarization on a list of message dicts."""
    conversation = _format_messages(msgs)
    if len(conversation) > MAX_INPUT_CHARS:
        conversation = conversation[-MAX_INPUT_CHARS:]
        # Trim to nearest complete message line
        nl = conversation.find("\n")
        if nl != -1:
            conversation = conversation[nl + 1:]
        logger.warning("Truncated conversation to %d chars for summarization", len(conversation))
    result = await ai_client.complete(TASK_SUMMARY_PROMPT + conversation, max_tokens=max_tokens)
    return result


async def summarize_from_buffer(
    chat_id: int, *, ai_client: AIClient, since: datetime | None = None,
) -> str:
    """Summarize messages from the SQLite buffer."""
    from app import db

    msgs = await db.get_buffered_messages(chat_id, since=since)
    if not msgs:
        return "Нет сообщений для суммаризации."

    logger.info(">>> SUMMARIZE: chat=%s, messages=%d", chat_id, len(msgs))
    result = await summarize_messages(msgs, ai_client=ai_client)
    logger.info("<<< SUMMARIZE RESPONSE:\n%s", result)
    return result


async def build_daily_overview(
    chat_summaries: list[tuple[str, str]], *, ai_client: AIClient,
    user_name: str = "",
) -> str:
    parts = []
    for name, summary in chat_summaries:
        short = summary[:500] + "..." if len(summary) > 500 else summary
        parts.append(f"--- {name} ---\n{short}")

    full_text = "\n\n".join(parts)
    logger.info(">>> DAILY OVERVIEW: %d chats, input length: %d chars", len(chat_summaries), len(full_text))

    prompt = DAILY_OVERVIEW_PROMPT
    if user_name:
        prompt += (
            f"\nЭтот обзор для {user_name}. "
            "Неотвеченные вопросы адресованные этому человеку — выдели ПЕРВЫМИ с пометкой ‼️.\n\n"
        )
    result = await ai_client.complete(prompt + full_text, max_tokens=1500)
    logger.info("<<< DAILY OVERVIEW RESPONSE:\n%s", result)
    return result
