"""Пасхалка: на слово «ситников» в любом тексте бот салютует «Аве, Цезарь!»
и тут же подкидывает случайную цитату Сенеки (генерит через Haiku — дёшево,
т.к. триггер может срабатывать часто). Историческая фича, возвращена из истории.

Регистрируется ПЕРЕД buffer (catch-all), но ПОСЛЕ всех FSM-роутеров — чтобы не
перехватывать сообщения у пользователя в активном FSM (там роутеры идут раньше).
"""
import logging
import random

from aiogram import F, Router
from aiogram.types import Message

from app.db import is_group_muted
from app.utils import strip_numbered_item

logger = logging.getLogger("arkadyjarvis")
router = Router()

SENECA_PROMPT = """\
Напиши 3 цитаты Сенеки (Луций Анней Сенека, стоик). \
Бери РАЗНЫЕ произведения: "Нравственные письма к Луцилию", "О краткости жизни", \
"О блаженной жизни", "О гневе", "О стойкости мудреца", "О провидении" и др. \
Цитаты должны быть глубокие, философские, про жизнь, время, смерть, мужество, судьбу. \
НЕ повторяй самые заезженные ("Пока мы откладываем жизнь..." и т.п.). \
Каждая цитата — 1-2 предложения. Формат:
1. ...
2. ...
3. ...
Без вступления, без указания источника, только сами цитаты. Каждый раз НОВЫЕ."""


def _pick_one(text: str) -> str:
    lines = [line.strip() for line in text.split("\n") if line.strip()]
    if not lines:
        return ""
    return strip_numbered_item(random.choice(lines))


@router.message(F.text, F.text.func(lambda t: bool(t) and "ситников" in t.lower()))
async def handle_sitnikov(message: Message, ai_client) -> None:
    # В замьюченных группах молчим (mute глушит и пасхалку).
    if message.chat.type in ("group", "supergroup") and await is_group_muted(
        message.chat.id
    ):
        return
    logger.info(
        "*** TRIGGER 'ситников' chat=%s user=%s",
        message.chat.id,
        message.from_user.id if message.from_user else None,
    )
    # Сразу — салют Цезарю.
    await message.reply("Аве, Цезарь!")
    # Затем — цитата Сенеки (генерация может занять пару секунд).
    try:
        text = await ai_client.complete(
            SENECA_PROMPT, model="claude-haiku-4-5-20251001",
        )
        quote = _pick_one(text)
        if quote:
            await message.answer(quote)
    except Exception as e:
        logger.warning("Seneca quote generation failed: %s", e)
