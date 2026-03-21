import logging
from datetime import datetime

from aiogram import F, Router
from aiogram.types import Message

logger = logging.getLogger("arkadyjarvis")
router = Router()


@router.message(F.text == "🏢 В офисе", F.chat.type == "private")
async def handle_office_start(message: Message, bitrix, db_user=None):
    await _start_work(message, bitrix, db_user, remote=False)


@router.message(F.text == "🏠 Удалённо", F.chat.type == "private")
async def handle_remote_start(message: Message, bitrix, db_user=None):
    await _start_work(message, bitrix, db_user, remote=True)


async def _start_work(message: Message, bitrix, db_user, remote: bool):
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
    if result.get("ok"):
        if remote:
            await message.answer("🏠 Рабочий день начат (удалённо)")
        else:
            await message.answer("🏢 Рабочий день начат")
    else:
        error = result.get("error", "Неизвестная ошибка")
        logger.error("Failed to start work day for user %s: %s", bitrix_id, error)
        await message.answer(f"❌ Не удалось начать день: {error}")
