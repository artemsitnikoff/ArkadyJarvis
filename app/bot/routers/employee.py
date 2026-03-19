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

from app.bot.routers.start import BACK_MENU_KB, MENU_KB

logger = logging.getLogger("arkadyjarvis")
router = Router()


class FindEmployee(StatesGroup):
    waiting_for_name = State()


@router.message(FindEmployee.waiting_for_name)
async def handle_employee_search(message: Message, state: FSMContext, bitrix):
    query = message.text.strip() if message.text else ""
    if not query:
        await message.answer("Напиши имя или фамилию сотрудника:", reply_markup=BACK_MENU_KB)
        return

    try:
        users = await bitrix.search_users(query, limit=10)
    except Exception as e:
        logger.error("Bitrix search error: %s", e, exc_info=True)
        await message.answer("❌ Ошибка поиска в Bitrix", reply_markup=MENU_KB)
        await state.clear()
        return

    if not users:
        await message.answer(
            f"🔍 По запросу «{html_mod.escape(query)}» никого не найдено.\n\nПопробуй другое имя:",
            reply_markup=BACK_MENU_KB,
        )
        return

    buttons = [
        [InlineKeyboardButton(
            text=u["name"],
            callback_data=f"emp:card:{u['id']}",
        )]
        for u in users
    ]
    buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")])
    await message.answer(
        f"🔍 Найдено по «{html_mod.escape(query)}»:",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


@router.callback_query(F.data.startswith("emp:card:"))
async def handle_employee_card(callback: CallbackQuery, state: FSMContext, bitrix):
    user_id = int(callback.data.split(":")[-1])
    await callback.answer()

    try:
        card = await bitrix.get_employee_card(user_id)
    except Exception as e:
        logger.error("Bitrix employee card error: %s", e, exc_info=True)
        await callback.message.answer("❌ Не удалось загрузить карточку", reply_markup=MENU_KB)
        return

    if not card:
        await callback.message.answer("❌ Сотрудник не найден", reply_markup=MENU_KB)
        return

    lines = [f"👤 <b>{html_mod.escape(card['name'])}</b>"]

    if card.get("position"):
        lines.append(f"💼 {html_mod.escape(card['position'])}")

    if card.get("departments"):
        dept_str = ", ".join(html_mod.escape(d) for d in card["departments"])
        lines.append(f"🏢 {dept_str}")

    if card.get("email"):
        lines.append(f"📧 {html_mod.escape(card['email'])}")

    if card.get("phone"):
        lines.append(f"📱 {html_mod.escape(card['phone'])}")

    if card.get("supervisor"):
        sup = card["supervisor"]
        sup_text = html_mod.escape(sup["name"])
        if sup.get("position"):
            sup_text += f" ({html_mod.escape(sup['position'])})"
        lines.append(f"\n👆 <b>Руководитель:</b> {sup_text}")

    buttons = [[InlineKeyboardButton(text="◀️ Меню", callback_data="back:menu")]]
    await callback.message.answer(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )
