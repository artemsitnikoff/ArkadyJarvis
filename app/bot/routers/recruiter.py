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
from app.config import settings
from app.services.potok_client import score_label
from app.services.potok_models import Applicant
from app.services.resume_scorer import extract_recruiter_instructions, score_applicant

logger = logging.getLogger("arkadyjarvis")
router = Router()

INTERVIEW_STAGE_NAME = "Интервью с рекрутером"


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


RECRUITER_ALLOWED = _parse_allowed_ids(settings.recruiter_allowed)



class Recruiter(StatesGroup):
    choosing_job = State()
    confirming = State()
    scoring = State()
    contacting = State()


def _filter_by_stage(applicants: list[Applicant], job_id: int, stage_name: str) -> list[Applicant]:
    result = []
    for a in applicants:
        for join in (a.ajs_joins or []):
            if (
                join.job and join.job.id == job_id
                and join.stage and join.stage.name.lower() == stage_name.lower()
            ):
                result.append(a)
                break
    return result


def _format_contact_card(applicant: Applicant) -> str:
    esc = html_mod.escape
    cv_params = applicant.resumes[0].cv_params if applicant.resumes else None

    lines = [f"👤 <b>{esc(applicant.display_name)}</b>"]

    title = applicant.title or (cv_params.title if cv_params else None)
    if title:
        lines.append(f"💼 {esc(title)}")
    if applicant.city:
        lines.append(f"📍 {esc(applicant.city.display_name)}")
    salary = str(applicant.salary) if applicant.salary else (str(cv_params.salary) if cv_params and cv_params.salary else None)
    if salary:
        lines.append(f"💰 {esc(salary)}")

    if cv_params:
        skills = cv_params.all_skills[:10]
        if skills:
            lines.append(f"\n🔧 <b>Навыки:</b> {esc(', '.join(skills))}")

        exp_items = cv_params.experience_items[:3]
        if exp_items:
            lines.append("\n📋 <b>Опыт:</b>")
            for exp in exp_items:
                period = f"{exp.start or '?'} — {exp.end or 'н.в.'}"
                lines.append(f"  • {esc(exp.company or '?')} — {esc(exp.position or '?')} ({period})")

        if cv_params.about_me:
            lines.append(f"\n📝 <b>О себе:</b> {esc(cv_params.about_me[:400])}")

    return "\n".join(lines)


def _format_result_message(
    job_name: str, idx: int, total: int, result, applicant_name: str,
) -> str:
    """Format full scoring result as a Telegram HTML message."""
    label = score_label(result.score)
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

    if result.questions:
        lines.append("")
        lines.append("💬 <b>Вопросы для первого контакта:</b>")
        for q in result.questions:
            lines.append(f"  • {html_mod.escape(q)}")

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
    clean_desc, recruiter_instructions = extract_recruiter_instructions(raw_desc)
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

    contact_applicants = _filter_by_stage(all_applicants, job_id, INTERVIEW_STAGE_NAME)
    total_all = len(all_applicants)
    total_new = len(new_applicants)
    total_contact = len(contact_applicants)
    logger.info(
        "Recruiter job %s: %d total, %d new, %d interview",
        job_id, total_all, total_new, total_contact,
    )

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
    if total_contact > 0:
        buttons.append([InlineKeyboardButton(
            text=f"📞 Связаться с кандидатами ({total_contact})",
            callback_data=f"recruit:contact:{job_id}",
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
        contact_applicants=contact_applicants,
    )


@router.callback_query(F.data.startswith("recruit:contact:"), Recruiter.confirming)
async def handle_contact_candidates(callback: CallbackQuery, state: FSMContext, userbot):
    await callback.answer()
    data = await state.get_data()
    contact_applicants = data.get("contact_applicants", [])
    job = data["job"]

    if not contact_applicants:
        await callback.message.answer(
            f"Нет кандидатов в стадии «{INTERVIEW_STAGE_NAME}».",
            reply_markup=MENU_KB,
        )
        await state.clear()
        return

    await state.set_state(Recruiter.contacting)

    userbot_ok = userbot is not None
    await callback.message.answer(
        f"📋 <b>Кандидаты для контакта: {len(contact_applicants)}</b>\n"
        f"Вакансия: {html_mod.escape(job.name)}\n"
        f"Стадия: «{INTERVIEW_STAGE_NAME}»"
        + ("" if userbot_ok else "\n\n⚠️ Userbot не настроен — кнопка «Написать» недоступна")
    )

    for applicant in contact_applicants:
        text = _format_contact_card(applicant)
        phones = applicant.phones or []
        if userbot_ok and phones:
            kb = InlineKeyboardMarkup(inline_keyboard=[[
                InlineKeyboardButton(
                    text=f"✉️ Написать",
                    callback_data=f"recruit:message:{applicant.id}",
                )
            ]])
        else:
            kb = None
        await callback.message.answer(text, reply_markup=kb)

    exit_kb = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="◀️ Меню", callback_data="recruit:exit")
    ]])
    await callback.message.answer(
        "Нажми «✉️ Написать» чтобы отправить сообщение кандидату со своего Telegram.",
        reply_markup=exit_kb,
    )


@router.callback_query(F.data.startswith("recruit:message:"), Recruiter.contacting)
async def handle_send_message(callback: CallbackQuery, state: FSMContext, userbot, potok):
    applicant_id = int(callback.data.split(":")[-1])
    data = await state.get_data()
    contact_applicants = data.get("contact_applicants", [])
    job = data["job"]

    applicant = next((a for a in contact_applicants if a.id == applicant_id), None)
    if not applicant:
        await callback.answer("Кандидат не найден", show_alert=True)
        return

    phones = applicant.phones or []
    if not phones:
        await callback.answer("У кандидата нет телефона в Potok", show_alert=True)
        return

    if not userbot:
        await callback.answer("Userbot не настроен", show_alert=True)
        return

    await callback.answer("Отправляю...")

    phone = phones[0]
    questions = await potok.get_applicant_questions(applicant_id)

    if not questions:
        await callback.answer("Нет вопросов для этого кандидата — сначала оцените его", show_alert=True)
        return

    # Intro: company + vacancy context
    company = settings.recruiter_company
    name_part = f"Меня зовут {settings.recruiter_name}, я" if settings.recruiter_name else "Я"
    intro = f"{name_part} представляю компанию {company}. Рассматриваю вас на вакансию «{job.name}»."
    await userbot.send_message(phone, intro)

    sent = 0
    for q in questions:
        if await userbot.send_message(phone, q):
            sent += 1
        else:
            break

    if sent > 0:
        try:
            await callback.message.edit_reply_markup(
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[[
                    InlineKeyboardButton(text="✅ Отправлено", callback_data="recruit:noop")
                ]])
            )
        except Exception:
            pass

        await callback.message.answer(
            f"✅ Отправлено <b>{html_mod.escape(applicant.display_name)}</b>: "
            f"{sent} вопрос{'ов' if sent != 1 else ''}"
        )
    else:
        await callback.message.answer(
            f"❌ Не удалось отправить <b>{html_mod.escape(applicant.display_name)}</b> "
            f"— нет Telegram или приватность закрыта"
        )


@router.callback_query(F.data == "recruit:noop")
async def handle_noop(callback: CallbackQuery):
    await callback.answer()


async def _run_scoring(
    callback: CallbackQuery, state: FSMContext, potok, ai_client, job, applicants,
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
            result = await score_applicant(job, applicant, ai_client=ai_client)

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
async def handle_score_new(callback: CallbackQuery, state: FSMContext, potok, ai_client):
    """Score only new (unscored) candidates."""
    await callback.answer()
    data = await state.get_data()
    await _run_scoring(
        callback, state, potok, ai_client, data["job"], data["new_applicants"],
    )


@router.callback_query(F.data.startswith("recruit:rescore:"), Recruiter.confirming)
async def handle_rescore_all(callback: CallbackQuery, state: FSMContext, potok, ai_client):
    """Re-score all candidates (including already scored)."""
    await callback.answer()
    data = await state.get_data()
    await _run_scoring(
        callback, state, potok, ai_client, data["job"], data["all_applicants"],
    )
