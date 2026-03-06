import html as html_mod
import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from app.bot.routers.start import MENU_KB
from app.services.resume_scorer import score_applicant
from app.services.resume_scorer import _extract_recruiter_instructions

logger = logging.getLogger("arkadyjarvis")
router = Router()

RECRUITER_ALLOWED = {33570147, 367140321, 421632942}  # Artem, Natalya, Liza


class Recruiter(StatesGroup):
    choosing_job = State()
    confirming = State()
    scoring = State()


def _score_label(score: int) -> str:
    if score >= 81:
        return "Отлично"
    if score >= 61:
        return "Хорошо"
    if score >= 41:
        return "Средне"
    return "Слабо"


def _format_result_message(
    job_name: str, idx: int, total: int, result, applicant_name: str,
) -> str:
    """Format full scoring result as a Telegram HTML message."""
    label = _score_label(result.score)
    name = html_mod.escape(applicant_name)
    jname = html_mod.escape(job_name)

    lines = [
        f"👔 <b>{jname}</b> [{idx}/{total}]",
        "",
        f"<b>{name}</b>",
        f"Балл: <b>{result.score}/100</b> ({label})",
        "",
        html_mod.escape(result.reasoning),
    ]

    if result.breakdown:
        lines.append("")
        lines.append("📊 <b>Разбивка по критериям:</b>")
        for b in result.breakdown:
            criterion = html_mod.escape(b.criterion)
            comment = html_mod.escape(b.comment) if b.comment else ""
            lines.append(f"  {criterion}: <b>{b.score}</b> — {comment}")

    if result.strengths:
        lines.append("")
        lines.append("✅ <b>Сильные стороны:</b>")
        for s in result.strengths:
            lines.append(f"  • {html_mod.escape(s)}")

    if result.weaknesses:
        lines.append("")
        lines.append("⚠️ <b>Слабые стороны:</b>")
        for w in result.weaknesses:
            lines.append(f"  • {html_mod.escape(w)}")

    return "\n".join(lines)


@router.callback_query(F.data == "recruit:exit")
async def handle_recruit_exit(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await callback.message.answer(
        "Выбери команду — покажу подсказку:",
        reply_markup=MENU_KB,
    )
    await callback.answer()


@router.callback_query(F.data.startswith("recruit:job:"), Recruiter.choosing_job)
async def handle_job_selected(callback: CallbackQuery, state: FSMContext, potok):
    """Show job info + candidate count, then 2 buttons: Score / Menu."""
    job_id = int(callback.data.split(":")[-1])
    await callback.answer()

    progress_msg = await callback.message.answer("👔 Загружаю вакансию и кандидатов...")

    try:
        job = await potok.get_job(job_id)
        applicants = await potok.get_applicants_for_job(job_id, limit=20, skip_scored=True)
    except Exception as e:
        logger.error("Potok error loading job %s: %s", job_id, e, exc_info=True)
        await progress_msg.edit_text(
            f"❌ Ошибка загрузки из Potok: {html_mod.escape(str(e))}",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    total = len(applicants)

    if not applicants:
        await progress_msg.edit_text(
            f"👔 <b>{html_mod.escape(job.name)}</b>\n\n"
            "Все кандидаты уже оценены (или нет кандидатов).",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    # Build info message
    raw_desc = job.description or ""
    clean_desc, recruiter_instructions = _extract_recruiter_instructions(raw_desc)
    job_name = html_mod.escape(job.name)

    info_lines = [f"👔 <b>{job_name}</b>", ""]
    if clean_desc:
        desc_text = clean_desc[:1500]
        info_lines.append(f"📋 <b>Описание:</b>\n{html_mod.escape(desc_text)}")
        info_lines.append("")
    if recruiter_instructions:
        info_lines.append(f"🎯 <b>Важно для CLAUDE:</b>\n{html_mod.escape(recruiter_instructions[:1500])}")
        info_lines.append("")
    info_lines.append(f"Кандидатов к оценке: <b>{total}</b>")

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"✅ Оценить кандидатов ({total})",
            callback_data=f"recruit:score:{job_id}",
        )],
        [InlineKeyboardButton(text="◀️ Меню", callback_data="recruit:exit")],
    ])

    try:
        await progress_msg.edit_text("\n".join(info_lines), reply_markup=confirm_kb)
    except Exception:
        await callback.message.answer("\n".join(info_lines), reply_markup=confirm_kb)

    await state.set_state(Recruiter.confirming)
    await state.update_data(job_id=job_id)


@router.callback_query(F.data.startswith("recruit:score:"), Recruiter.confirming)
async def handle_start_scoring(callback: CallbackQuery, state: FSMContext, potok):
    """User confirmed — start scoring."""
    job_id = int(callback.data.split(":")[-1])
    await callback.answer()

    await state.set_state(Recruiter.scoring)

    # Reload job + applicants (fresh data)
    try:
        job = await potok.get_job(job_id)
        applicants = await potok.get_applicants_for_job(job_id, limit=20, skip_scored=True)
    except Exception as e:
        logger.error("Potok error: %s", e, exc_info=True)
        await callback.message.answer(
            f"❌ Ошибка: {html_mod.escape(str(e))}",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    if not applicants:
        await callback.message.answer(
            f"👔 <b>{html_mod.escape(job.name)}</b>\n\n"
            "Все кандидаты уже оценены.",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    total = len(applicants)
    job_name = job.name
    scored = 0
    errors = 0

    for i, applicant in enumerate(applicants, 1):
        name = applicant.display_name

        # Show "thinking" message
        thinking_msg = await callback.message.answer(
            f"👔 <b>{html_mod.escape(job_name)}</b> [{i}/{total}]\n\n"
            f"⏳ {html_mod.escape(name)}..."
        )

        try:
            result = await score_applicant(job, applicant)

            # Edit thinking message with full result
            text = _format_result_message(job_name, i, total, result, name)
            try:
                await thinking_msg.edit_text(text)
            except Exception:
                await thinking_msg.delete()
                await callback.message.answer(text)

            scored += 1

            # Push to Potok
            try:
                await potok.push_scoring(
                    result, job_id,
                    original_last_name=applicant.last_name or "",
                )
            except Exception as e:
                logger.error("Potok push error for %s: %s", applicant.id, e)

        except Exception as e:
            logger.error("Scoring error for %s: %s", name, e, exc_info=True)
            try:
                await thinking_msg.edit_text(
                    f"👔 <b>{html_mod.escape(job_name)}</b> [{i}/{total}]\n\n"
                    f"❌ {html_mod.escape(name)} — ошибка: {html_mod.escape(str(e)[:200])}"
                )
            except Exception:
                pass
            errors += 1

    # Final summary
    summary = (
        f"👔 <b>{html_mod.escape(job_name)}</b> — готово!\n\n"
        f"Оценено: {scored} | Ошибок: {errors}"
    )
    await callback.message.answer(summary, reply_markup=MENU_KB)
    await state.clear()
