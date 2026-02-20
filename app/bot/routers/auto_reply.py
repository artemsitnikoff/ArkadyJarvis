import logging
import random

from aiogram import F, Router
from aiogram.types import Message

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
    return strip_numbered_item(random.choice(lines))


@router.message(F.text, F.text.func(lambda t: "ситников" in t.lower()))
async def handle_sitnikov(message: Message, ai_client):
    logger.info("*** TRIGGER: 'ситников' in chat=%s from user=%s", message.chat.id, message.from_user.id)
    text = await ai_client.complete(SENECA_PROMPT, max_tokens=500, temperature=1.2)
    quote = _pick_one(text)
    await message.reply(quote)
