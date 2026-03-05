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

logger = logging.getLogger("arkadyjarvis")
router = Router()

RECRUITER_ALLOWED = {33570147}  # Artem Sitnikov

RECRUITER_EXIT_KB = InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="◀️ Меню", callback_data="recruit:exit")],
])


class Recruiter(StatesGroup):
    choosing_job = State()
    scoring = State()


def _score_label(score: int) -> str:
    if score >= 81:
        return "Отлично"
    if score >= 61:
        return "Хорошо"
    if score >= 41:
        return "Средне"
    return "Слабо"


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
            reply_markup=RECRUITER_EXIT_KB,
        )
        await state.clear()
        return

    if not applicants:
        await progress_msg.edit_text(
            f"👔 <b>{html_mod.escape(job.name)}</b>\n\n"
            "Все кандидаты уже оценены (или нет кандидатов).",
            reply_markup=RECRUITER_EXIT_KB,
        )
        await state.clear()
        return

    await state.set_state(Recruiter.scoring)
    total = len(applicants)
    job_name = html_mod.escape(job.name)
    lines: list[str] = []

    for i, applicant in enumerate(applicants, 1):
        name = html_mod.escape(applicant.display_name)

        # Update progress
        progress_lines = "\n".join(lines) + (f"\n⏳ {name}..." if lines else f"⏳ {name}...")
        try:
            await progress_msg.edit_text(
                f"👔 <b>{job_name}</b>\n\n{progress_lines}\n\n[{i}/{total}]",
                reply_markup=RECRUITER_EXIT_KB,
            )
        except Exception:
            pass

        try:
            result = await score_applicant(job, applicant)
            label = _score_label(result.score)
            lines.append(f"✅ {result.score} {name} — {label}")

            # Push to Potok
            try:
                await potok.push_scoring(
                    result, job_id,
                    original_last_name=applicant.last_name or "",
                )
            except Exception as e:
                logger.error("Potok push error for %s: %s", applicant.id, e)

        except Exception as e:
            logger.error("Scoring error for %s: %s", applicant.display_name, e, exc_info=True)
            lines.append(f"❌ {name} — ошибка")

        # Update with result
        try:
            await progress_msg.edit_text(
                f"👔 <b>{job_name}</b>\n\n" + "\n".join(lines) + f"\n\n[{i}/{total}]",
                reply_markup=RECRUITER_EXIT_KB,
            )
        except Exception:
            pass

    # Final summary
    try:
        await progress_msg.edit_text(
            f"👔 <b>{job_name}</b> — готово!\n\n" + "\n".join(lines),
            reply_markup=RECRUITER_EXIT_KB,
        )
    except Exception:
        pass

    await state.clear()
