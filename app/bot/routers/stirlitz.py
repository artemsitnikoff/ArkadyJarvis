"""Штирлиц — кнопка разведки контрагента по ИНН/названию."""
import html as html_mod
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from app.bot.routers.start import BACK_MENU_KB, MENU_KB
from app.services.stirlitz import build_intel_card

logger = logging.getLogger("arkadyjarvis")
router = Router()


class Stirlitz(StatesGroup):
    waiting_for_query = State()


@router.callback_query(F.data == "hint:stirlitz")
async def handle_open(callback: CallbackQuery, state: FSMContext):
    await state.set_state(Stirlitz.waiting_for_query)
    await callback.message.answer(
        "🕵️ <b>Штирлиц</b> — разведка по контрагенту\n\n"
        "Введи <b>ИНН</b> (10 или 12 цифр) или <b>название компании</b>. "
        "Соберу профиль из открытых источников (ЕГРЮЛ + бухотчётность ФНС) "
        "и подготовлю карточку для продажника.",
        reply_markup=BACK_MENU_KB,
    )
    await callback.answer()


@router.message(Stirlitz.waiting_for_query, F.text)
async def handle_query(
    message: Message,
    state: FSMContext,
    ai_client,
    dadata,
    giro,
):
    query = (message.text or "").strip()
    if not query:
        await message.reply("Введи ИНН или название компании.")
        return

    await state.clear()
    wait = await message.reply(f"🕵️ Веду разведку: <b>{html_mod.escape(query)}</b>…")

    try:
        card, suggestions, err = await build_intel_card(query, ai_client, dadata, giro)
    except Exception as e:
        logger.error("Stirlitz error: %s", e, exc_info=True)
        await wait.edit_text(
            f"❌ Сбой при разведке: {html_mod.escape(str(e)[:200])}",
            reply_markup=MENU_KB,
        )
        return

    if err and not card:
        await wait.edit_text(f"❌ {html_mod.escape(err)}", reply_markup=MENU_KB)
        return

    # Отдаём карточку. Если длинная — прикрепляем .md, в чат — превью
    if card and len(card) <= 4000:
        await wait.edit_text(card, reply_markup=MENU_KB)
    elif card:
        try:
            await wait.delete()
        except Exception:
            pass
        preview = card[:1500] + "\n\n[…полная разведка во вложении]"
        file = BufferedInputFile(card.encode("utf-8"), filename=f"intel_{query[:20]}.md")
        await message.answer_document(
            file,
            caption=preview,
            reply_markup=MENU_KB,
        )

    # Если ввели название и было несколько вариантов — покажем
    if suggestions and len(suggestions) > 1:
        lines = ["", "<i>Найдены ещё варианты по запросу:</i>"]
        for s in suggestions[1:5]:
            name = html_mod.escape(s.get("value") or "")
            inn = (s.get("data") or {}).get("inn") or ""
            lines.append(f"  • <code>{inn}</code> — {name}")
        await message.answer("\n".join(lines))
