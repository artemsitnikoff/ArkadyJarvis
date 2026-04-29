import html as html_mod

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from app.bot.routers.start import BACK_MENU_KB, MENU_KB

router = Router()


class TestReverse(StatesGroup):
    waiting_for_text = State()


@router.callback_query(F.data == "test:start")
async def handle_test_start(callback: CallbackQuery, state: FSMContext):
    await state.set_state(TestReverse.waiting_for_text)
    await callback.message.answer(
        "🧪 Напиши слово или текст — верну его задом наперёд.",
        reply_markup=BACK_MENU_KB,
    )
    await callback.answer()


@router.message(TestReverse.waiting_for_text, F.text)
async def handle_test_text(message: Message, state: FSMContext):
    reversed_text = message.text[::-1]
    await message.answer(
        f"🔁 <b>{html_mod.escape(reversed_text)}</b>",
        reply_markup=MENU_KB,
    )
    await state.clear()
