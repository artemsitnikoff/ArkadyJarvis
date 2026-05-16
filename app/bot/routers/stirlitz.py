"""Штирлиц — кнопка разведки. Diff vs прошлой версии: классификация через
Claude (Haiku), уточняющие вопросы, история сообщений в FSM."""
import html as html_mod
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from app.bot.routers.start import BACK_MENU_KB, MENU_KB
from app.services.stirlitz import (
    build_company_card,
    build_person_card,
    classify_intent,
)

logger = logging.getLogger("arkadyjarvis")
router = Router()

# История запросов кандидата ограничена — чтобы LLM-классификатор не получал
# слишком длинный контекст при нескольких уточнениях подряд.
MAX_HISTORY = 6


class Stirlitz(StatesGroup):
    waiting_for_query = State()


# Note: «hint:stirlitz» button click is handled by the generic hint dispatcher
# in app/bot/routers/start.py — it picks up the FSM state + prompt from
# _simple_fsm_hints(). The text input below handles all user messages.


@router.message(Stirlitz.waiting_for_query, F.text)
async def handle_query(
    message: Message,
    state: FSMContext,
    ai_client,
    dadata,
    giro,
):
    text = (message.text or "").strip()
    if not text:
        await message.reply("Пиши текстом — ИНН, название компании или ФИО.")
        return

    # Накапливаем историю сообщений (для уточнений)
    data = await state.get_data()
    history = (data.get("history") or []) + [text]
    history = history[-MAX_HISTORY:]
    await state.update_data(history=history)

    wait = await message.reply("🕵️ Думаю что искать…")

    intent = await classify_intent(history, ai_client)
    kind = intent.get("kind")
    logger.info("Stirlitz intent for history=%r: %r", history, intent)

    if kind == "clarify":
        q = intent.get("question") or "Уточни запрос."
        try:
            await wait.edit_text(
                f"🤔 {html_mod.escape(q)}",
                reply_markup=BACK_MENU_KB,
            )
        except Exception:
            await message.reply(f"🤔 {html_mod.escape(q)}", reply_markup=BACK_MENU_KB)
        # FSM остаётся — ждём следующее сообщение от пользователя
        return

    # Готовы искать — чистим историю на следующий заход
    await state.update_data(history=[])

    try:
        await wait.edit_text("🕵️ Веду разведку…")
    except Exception:
        pass

    if kind == "person":
        card, suggestions, err = await build_person_card(intent, ai_client)
    elif kind in ("company_inn", "company_name"):
        card, suggestions, err = await build_company_card(intent, ai_client, dadata, giro)
    else:
        # На случай если классификатор вернёт что-то странное
        await wait.edit_text(
            f"❌ Не понял запрос (kind={kind!r}). Попробуй ещё раз.",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    if err and not card:
        await wait.edit_text(f"❌ {html_mod.escape(err)}", reply_markup=MENU_KB)
        await state.clear()
        return

    # Карточка готова — отдаём в чат
    if card and len(card) <= 4000:
        try:
            await wait.edit_text(card, reply_markup=MENU_KB)
        except Exception:
            await message.answer(card, reply_markup=MENU_KB)
    elif card:
        try:
            await wait.delete()
        except Exception:
            pass
        preview = card[:1500] + "\n\n[…полная разведка во вложении]"
        file = BufferedInputFile(card.encode("utf-8"), filename="intel.md")
        await message.answer_document(file, caption=preview, reply_markup=MENU_KB)

    # Альтернативы по поиску компании
    if suggestions and len(suggestions) > 1 and kind != "person":
        lines = ["", "<i>Найдены ещё варианты по запросу:</i>"]
        for s in suggestions[1:5]:
            name = html_mod.escape(s.get("value") or "")
            inn = (s.get("data") or {}).get("inn") or ""
            lines.append(f"  • <code>{inn}</code> — {name}")
        await message.answer("\n".join(lines))

    await state.clear()
