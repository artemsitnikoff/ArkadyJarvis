import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.types import Message

from app.utils import md_to_telegram_html

logger = logging.getLogger("arkadyjarvis")
router = Router()

GREETING_PROMPT = (
    "Сгенерируй короткое (2-3 предложения) мотивирующее приветствие для сотрудника, "
    "который начинает рабочий день. Обращайся на 'ты'. "
    "Будь тёплым, искренним и вдохновляющим. Добавь один эмодзи в конце. "
    "Не используй клише. Каждый раз генерируй уникальное приветствие."
)


@router.message(F.text == "🏢 В офисе", F.chat.type == "private")
async def handle_office_start(message: Message, bitrix, ai_client, db_user=None):
    await _start_work(message, bitrix, ai_client, db_user, remote=False)


@router.message(F.text == "🏠 Удалённо", F.chat.type == "private")
async def handle_remote_start(message: Message, bitrix, ai_client, db_user=None):
    await _start_work(message, bitrix, ai_client, db_user, remote=True)


async def _start_work(message: Message, bitrix, ai_client, db_user, remote: bool):
    if not db_user or not db_user.get("bitrix_user_id"):
        await message.answer("❌ Сначала авторизуйся: /start")
        return

    bitrix_id = db_user["bitrix_user_id"]

    # Check if already working
    status = await bitrix.get_work_status(bitrix_id)
    if status and status.get("status") == "OPENED":
        time_start = status.get("time_start", "")
        time_str = ""
        if time_start:
            try:
                if "T" in time_start:
                    dt = datetime.fromisoformat(time_start)
                else:
                    dt = datetime.strptime(time_start, "%d.%m.%Y %H:%M:%S")
                time_str = f" с {dt.strftime('%H:%M')}"
            except (ValueError, TypeError):
                pass
        await message.answer(f"✅ Вы уже работаете{time_str}")
        return

    # Start work day
    result = await bitrix.start_work_day(bitrix_id, remote=remote)
    if not result.get("ok"):
        error = result.get("error", "Неизвестная ошибка")
        logger.error("Failed to start work day for user %s: %s", bitrix_id, error)
        await message.answer(f"❌ Не удалось начать день: {error}")
        return

    # Generate motivational greeting via Claude
    name = db_user.get("display_name") or "коллега"
    place = "удалённо" if remote else "в офисе"
    icon = "🏠" if remote else "🏢"

    try:
        greeting = await ai_client.complete(
            f"{GREETING_PROMPT}\nИмя сотрудника: {name}. Работает сегодня {place}."
        )
        html_greeting = md_to_telegram_html(greeting)
        await message.answer(f"{icon} Рабочий день начат\n\n{html_greeting}")
    except Exception as e:
        logger.warning("Greeting generation failed: %s", e)
        await message.answer(f"{icon} Рабочий день начат ({place})")
