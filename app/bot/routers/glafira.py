import asyncio
import html as html_mod
import logging

from aiogram import F, Router
from aiogram.exceptions import TelegramBadRequest, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.routers.start import MENU_KB
from app.config import settings

logger = logging.getLogger("arkadyjarvis")
router = Router()


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


GLAFIRA_ALLOWED = _parse_allowed_ids(settings.glafira_allowed)

GLAFIRA_EXIT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="◀️ Меню", callback_data="glafira:exit")],
])

MAX_HISTORY = 20


class Glafira(StatesGroup):
    chatting = State()


@router.callback_query(F.data == "glafira:exit")
async def handle_glafira_exit(callback: CallbackQuery, state: FSMContext):
    """Exit Glafira conversation mode."""
    await state.clear()
    await callback.message.answer(
        "Выбери команду — покажу подсказку:",
        reply_markup=MENU_KB,
    )
    await callback.answer()


@router.message(Glafira.chatting, F.text)
async def handle_glafira_message(
    message: Message, state: FSMContext, openclaw,
):
    """Forward user message to OpenClaw and stream response back."""
    user_text = (message.text or "").strip()
    if not user_text:
        return

    # Build conversation history
    data = await state.get_data()
    conv_messages: list[dict] = data.get("messages", [])
    conv_messages.append({"role": "user", "content": user_text})

    if len(conv_messages) > MAX_HISTORY:
        conv_messages = conv_messages[-MAX_HISTORY:]

    wait_msg = await message.reply("🤖 Думаю...")

    try:
        full_text = ""
        last_edit_len = 0
        edit_interval = 0.8
        last_edit_time = 0.0

        async for chunk in openclaw.stream_chat(conv_messages, user_id=message.from_user.id):
            full_text += chunk

            now = asyncio.get_event_loop().time()
            if (now - last_edit_time >= edit_interval
                    and len(full_text) - last_edit_len >= 20):
                try:
                    display = html_mod.escape(full_text[:4000])
                    await wait_msg.edit_text(
                        f"🤖 {display}",
                        reply_markup=GLAFIRA_EXIT_KB,
                    )
                    last_edit_len = len(full_text)
                    last_edit_time = now
                except TelegramRetryAfter as e:
                    logger.warning("Glafira stream rate-limited, backing off %ss", e.retry_after)
                    await asyncio.sleep(e.retry_after)
                except TelegramBadRequest as e:
                    # Ignore "message is not modified"; log other parse issues
                    if "not modified" not in str(e):
                        logger.warning("Glafira stream edit_text failed: %s", e)

        # Final edit with complete text
        if full_text.strip():
            display = html_mod.escape(full_text.strip()[:4000])
            final_msg = f"🤖 {display}"
            try:
                await wait_msg.edit_text(
                    final_msg,
                    reply_markup=GLAFIRA_EXIT_KB,
                )
            except TelegramBadRequest as e:
                if "not modified" not in str(e):
                    logger.warning("Glafira final edit_text failed: %s", e)
        else:
            await wait_msg.edit_text(
                "🤖 Марфа не ответила. Попробуй переформулировать.",
                reply_markup=GLAFIRA_EXIT_KB,
            )

        # Save assistant response to conversation history
        conv_messages.append({"role": "assistant", "content": full_text})
        await state.update_data(messages=conv_messages)

    except Exception as e:
        logger.error("Glafira error: %s", e, exc_info=True)
        await wait_msg.edit_text(
            f"❌ Ошибка связи с Марфой: {html_mod.escape(str(e))}",
            reply_markup=GLAFIRA_EXIT_KB,
        )
