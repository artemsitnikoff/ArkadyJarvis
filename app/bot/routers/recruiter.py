import html as html_mod
import logging
import re

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


@router.callback_query(F.data == "recruit:stop")
async def handle_recruit_stop(callback: CallbackQuery, state: FSMContext):
    await state.update_data(stop=True)
    await callback.answer("Останавливаю после текущего кандидата...")


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
    """Load job → show description + buttons. Candidates loaded after button click."""
    job_id = int(callback.data.split(":")[-1])
    await callback.answer()

    progress_msg = await callback.message.answer("👔 Загружаю вакансию...")

    try:
        job = await potok.get_job(job_id)
    except Exception as e:
        logger.error("Potok error loading job %s: %s", job_id, e, exc_info=True)
        await progress_msg.edit_text(
            f"❌ Ошибка загрузки из Potok: {html_mod.escape(str(e))}",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    raw_desc = job.description or ""
    clean_desc, recruiter_instructions = _extract_recruiter_instructions(raw_desc)
    job_name = html_mod.escape(job.name)

    info_lines = [f"👔 <b>{job_name}</b>", ""]
    if clean_desc:
        info_lines.append(f"📋 <b>Описание:</b>\n{html_mod.escape(clean_desc[:1500])}")
        info_lines.append("")
    if recruiter_instructions:
        info_lines.append(f"🎯 <b>Важно для CLAUDE:</b>\n{html_mod.escape(recruiter_instructions[:1500])}")
        info_lines.append("")
    info_lines.append("⏳ Считаю кандидатов...")

    try:
        await progress_msg.edit_text("\n".join(info_lines))
    except Exception:
        pass

    # Load candidates to get counts
    try:
        all_applicants = await potok.get_applicants_for_job(
            job_id, limit=0, skip_scored=False,
        )
        new_applicants = [
            a for a in all_applicants
            if not re.match(r"^\d{3}-", a.last_name or "")
        ]
    except Exception as e:
        logger.error("Potok error loading applicants: %s", e, exc_info=True)
        info_lines[-1] = f"❌ Ошибка загрузки кандидатов: {html_mod.escape(str(e))}"
        try:
            await progress_msg.edit_text("\n".join(info_lines), reply_markup=MENU_KB)
        except Exception:
            pass
        await state.clear()
        return

    total_all = len(all_applicants)
    total_new = len(new_applicants)
    logger.info("Recruiter job %s: %d total, %d new", job_id, total_all, total_new)

    if total_all == 0:
        info_lines[-1] = "Нет кандидатов на эту вакансию."
        try:
            await progress_msg.edit_text("\n".join(info_lines), reply_markup=MENU_KB)
        except Exception:
            pass
        await state.clear()
        return

    # Replace loading line with buttons
    info_lines.pop()  # remove "⏳ Считаю кандидатов..."

    buttons = []
    if total_new > 0:
        buttons.append([InlineKeyboardButton(
            text=f"✅ Оценить новых ({total_new})",
            callback_data=f"recruit:score:{job_id}",
        )])
    buttons.append([InlineKeyboardButton(
        text=f"🔄 Переоценить всех ({total_all})",
        callback_data=f"recruit:rescore:{job_id}",
    )])
    buttons.append([InlineKeyboardButton(text="◀️ Меню", callback_data="recruit:exit")])

    try:
        await progress_msg.edit_text(
            "\n".join(info_lines),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
        )
    except Exception:
        pass

    await state.set_state(Recruiter.confirming)
    await state.update_data(
        job_id=job_id,
        job=job,
        all_applicants=all_applicants,
        new_applicants=new_applicants,
    )


async def _run_scoring(
    callback: CallbackQuery, state: FSMContext, potok, job, applicants,
):
    """Common scoring loop for both new and rescore modes."""
    await state.set_state(Recruiter.scoring)

    job_id = job.id
    total = len(applicants)
    job_name = job.name
    scored = 0
    errors = 0

    stop_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⏹ Остановить", callback_data="recruit:stop")],
    ])

    for i, applicant in enumerate(applicants, 1):
        # Check if user requested stop
        data = await state.get_data()
        if data.get("stop"):
            break

        name = applicant.display_name

        thinking_msg = await callback.message.answer(
            f"👔 <b>{html_mod.escape(job_name)}</b> [{i}/{total}]\n\n"
            f"⏳ {html_mod.escape(name)}...",
            reply_markup=stop_kb,
        )

        try:
            result = await score_applicant(job, applicant)

            text = _format_result_message(job_name, i, total, result, name)
            if len(text) > 4096:
                text = text[:4090] + "\n…"
            try:
                await thinking_msg.edit_text(text)
            except Exception:
                await thinking_msg.delete()
                await callback.message.answer(text)

            scored += 1

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

    data = await state.get_data()
    stopped = data.get("stop", False)
    status = "остановлено" if stopped else "готово"
    summary = (
        f"👔 <b>{html_mod.escape(job_name)}</b> — {status}!\n\n"
        f"Оценено: {scored}/{total} | Ошибок: {errors}"
    )
    await callback.message.answer(summary, reply_markup=MENU_KB)
    await state.clear()


@router.callback_query(F.data.startswith("recruit:score:"), Recruiter.confirming)
async def handle_score_new(callback: CallbackQuery, state: FSMContext, potok):
    """Score only new (unscored) candidates."""
    await callback.answer()
    data = await state.get_data()
    await _run_scoring(callback, state, potok, data["job"], data["new_applicants"])


@router.callback_query(F.data.startswith("recruit:rescore:"), Recruiter.confirming)
async def handle_rescore_all(callback: CallbackQuery, state: FSMContext, potok):
    """Re-score all candidates (including already scored)."""
    await callback.answer()
    data = await state.get_data()
    await _run_scoring(callback, state, potok, data["job"], data["all_applicants"])
