import asyncio
import html as html_mod
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.routers.start import MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()

# Telegram IDs allowed to use Glafira (test mode)
GLAFIRA_ALLOWED = {33570147, 367140321}

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

        async for chunk in openclaw.stream_chat(conv_messages):
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
                except Exception:
                    pass

        # Final edit with complete text
        if full_text.strip():
            display = html_mod.escape(full_text.strip()[:4000])
            final_msg = f"🤖 {display}"
            try:
                await wait_msg.edit_text(
                    final_msg,
                    reply_markup=GLAFIRA_EXIT_KB,
                )
            except Exception:
                # Already showing same text — ignore "message is not modified"
                pass
        else:
            await wait_msg.edit_text(
                "🤖 Глафира не ответила. Попробуй переформулировать.",
                reply_markup=GLAFIRA_EXIT_KB,
            )

        # Save assistant response to conversation history
        conv_messages.append({"role": "assistant", "content": full_text})
        await state.update_data(messages=conv_messages)

    except Exception as e:
        logger.error("Glafira error: %s", e, exc_info=True)
        await wait_msg.edit_text(
            f"❌ Ошибка связи с Глафирой: {html_mod.escape(str(e))}",
            reply_markup=GLAFIRA_EXIT_KB,
        )
