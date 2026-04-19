"""Socrates — meeting analyser.

User flow:
  1. Press "🎓 Сократ" → enter FSM waiting_for_url.
  2. Send a URL (Yandex.Disk public link or direct download URL).
  3. Bot downloads → ffmpeg → transcribe → review → brief.
  4. Three .md files are attached to the chat.

Telegram Bot API caps uploads at 20 MB, so direct file uploads are not
supported — the recording MUST be shared via a link.

Access is gated via SOCRATES_ALLOWED — Socrates runs Gemini + Claude
twice + downloads ≤ 1 GiB, so we keep it behind an allow-list like
Glafira and Anatoly.
"""

import asyncio
import logging
import re
import shutil
import tempfile
from pathlib import Path
from typing import NoReturn

from aiogram import F, Router
from aiogram.exceptions import TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import BufferedInputFile, Message

from app.bot.routers.start import MENU_KB
from app.config import settings
from app.services.ffmpeg_tool import FFmpegError, convert_to_opus, probe_duration
from app.services.meeting_downloader import DownloadError, download_meeting
from app.services.meeting_pipeline import process_meeting

logger = logging.getLogger("arkadyjarvis")
router = Router()

URL_RE = re.compile(r"^https?://\S+$", re.IGNORECASE)


def _parse_allowed_ids(csv: str) -> set[int]:
    if not csv.strip():
        return set()
    return {int(x.strip()) for x in csv.split(",") if x.strip().isdigit()}


SOCRATES_ALLOWED = _parse_allowed_ids(settings.socrates_allowed)

# Per-user concurrency limit: one pipeline at a time per Telegram user.
# Prevents a user from firing 10 parallel 90-min pipelines (each spends
# Gemini + Claude ×2 + ≤1 GiB traffic).
_USER_LOCKS: dict[int, asyncio.Lock] = {}
_LOCKS_GUARD = asyncio.Lock()


async def _get_user_lock(user_id: int) -> asyncio.Lock:
    async with _LOCKS_GUARD:
        lock = _USER_LOCKS.get(user_id)
        if lock is None:
            lock = asyncio.Lock()
            _USER_LOCKS[user_id] = lock
        return lock


class Socrates(StatesGroup):
    waiting_for_url = State()


class _StageAbort(Exception):
    """Raised by stage helpers to skip the remainder of the pipeline.

    The user has already been notified via wait_msg.edit_text; the main
    handler just needs to exit cleanly and run the cleanup finally.
    """


# ── Handlers ──────────────────────────────────────────────────


@router.message(Socrates.waiting_for_url, F.text)
async def handle_meeting_url(
    message: Message, state: FSMContext, openrouter, ai_client,
):
    if SOCRATES_ALLOWED and message.from_user.id not in SOCRATES_ALLOWED:
        await state.clear()
        await message.reply(
            "🚧 Сократ в тестовом режиме. Доступ ограничен.",
            reply_markup=MENU_KB,
        )
        return

    url = (message.text or "").strip()
    if not URL_RE.match(url):
        await message.reply(
            "Пришли ссылку на запись (http(s)://...). "
            "Yandex.Диск / Telemost или прямой URL."
        )
        return

    user_lock = await _get_user_lock(message.from_user.id)
    if user_lock.locked():
        await state.clear()
        await message.reply(
            "⏳ Уже обрабатываю твою предыдущую запись. Дождись окончания "
            "и пришли следующую ссылку.",
            reply_markup=MENU_KB,
        )
        return

    await state.clear()
    logger.info("*** SOCRATES: url=%s from user=%s", url[:120], message.from_user.id)

    async with user_lock:
        wait_msg = await message.reply("📥 Скачиваю запись...")
        tmpdir = Path(tempfile.mkdtemp(prefix="socrates_"))
        raw_path = tmpdir / "source.bin"
        ogg_path = tmpdir / "audio.ogg"

        try:
            size_mb = await _download_stage(url, raw_path, wait_msg)
            duration_sec = await _convert_and_probe_stage(
                raw_path, ogg_path, size_mb, wait_msg,
            )
            artifacts = await _run_pipeline_stage(
                ogg_path, duration_sec, url, openrouter, ai_client, wait_msg,
            )
            await _deliver_artifacts(message, wait_msg, artifacts)
        except _StageAbort:
            # User already notified by the stage helper.
            return
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


@router.message(Socrates.waiting_for_url)
async def handle_meeting_not_text(message: Message):
    await message.reply(
        "Пришли ссылку на запись (текстовым сообщением). "
        "Файлы-видео напрямую не принимаю — Telegram режет upload на 20 МБ."
    )


# ── Stage helpers ─────────────────────────────────────────────


async def _download_stage(url: str, raw_path: Path, wait_msg: Message) -> float:
    """Stage 0a. Returns downloaded size in MiB. Aborts on failure."""
    try:
        size_bytes = await download_meeting(url, raw_path)
    except DownloadError as e:
        await _abort(wait_msg, f"❌ Не смог скачать: {e}")
    size_mb = size_bytes / (1024 * 1024)
    await _safe_edit(wait_msg, f"🎚 Скачано {size_mb:.1f} МБ. Проверяю длительность...")
    return size_mb


async def _convert_and_probe_stage(
    raw_path: Path, ogg_path: Path, size_mb: float, wait_msg: Message,
) -> float:
    """Stages 0b + 0c: probe duration → reject if too long → ffmpeg → re-probe if needed.

    Returns duration in seconds (0.0 if unknown).
    """
    # Probe BEFORE ffmpeg so we don't convert a 10-hour file just to reject it.
    try:
        duration_sec = await probe_duration(raw_path)
    except FFmpegError as e:
        logger.warning("probe_duration on raw file failed: %s — will retry on ogg", e)
        duration_sec = 0.0

    if duration_sec > 0:
        await _reject_if_too_long(duration_sec, wait_msg)

    if duration_sec > 0:
        duration_min = duration_sec / 60
        await _safe_edit(
            wait_msg,
            f"🎚 Скачано {size_mb:.1f} МБ, {duration_min:.1f} мин. "
            "Конвертирую аудио (ffmpeg)...",
        )
    else:
        # Duration unknown — don't show "0.0 мин", just say we're converting.
        await _safe_edit(
            wait_msg,
            f"🎚 Скачано {size_mb:.1f} МБ. Конвертирую аудио (ffmpeg)...",
        )

    try:
        await convert_to_opus(raw_path, ogg_path)
    except FFmpegError as e:
        logger.error("ffmpeg failed: %s", e)
        await _abort(wait_msg, f"❌ Не смог обработать аудио (ffmpeg): {e}")

    # Fallback: if the raw probe failed, try the converted audio.
    if duration_sec == 0.0:
        try:
            duration_sec = await probe_duration(ogg_path)
            await _reject_if_too_long(duration_sec, wait_msg)
            duration_min = duration_sec / 60
        except FFmpegError:
            pass  # keep 0 — pipeline can still run

    ogg_size_mb = ogg_path.stat().st_size / (1024 * 1024)
    await _safe_edit(
        wait_msg,
        f"🎙 Аудио {ogg_size_mb:.1f} МБ, длительность {duration_min:.1f} мин.\n"
        "Транскрибирую запись (диаризация)...",
    )
    return duration_sec


async def _run_pipeline_stage(
    ogg_path: Path, duration_sec: float, url: str, openrouter, ai_client,
    wait_msg: Message,
):
    """Stages 1-3. Returns MeetingArtifacts. Aborts on failure."""

    async def on_progress(msg: str):
        await _safe_edit(wait_msg, f"🧠 {msg}")

    try:
        return await process_meeting(
            ogg_path,
            openrouter=openrouter,
            ai_client=ai_client,
            source_name=_source_name_from_url(url),
            duration_sec=duration_sec,
            on_progress=on_progress,
        )
    except Exception:
        # Full traceback to log, generic message to user — never leak
        # library internals / file paths / payload fragments.
        logger.error("meeting pipeline failed", exc_info=True)
        await _abort(wait_msg, "❌ Пайплайн упал. Детали — в логах бота.")


async def _deliver_artifacts(message: Message, wait_msg: Message, artifacts) -> None:
    await _safe_edit(wait_msg, "📎 Готово, отправляю артефакты...")

    for name, body in [
        ("1_transcript.md", artifacts.transcript_md),
        ("2_review.md", artifacts.review_md),
        ("3_brief.md", artifacts.brief_md),
    ]:
        file = BufferedInputFile(body.encode("utf-8"), filename=name)
        await message.answer_document(file)

    await message.answer(
        "✅ Готово — транскрипт, ревью и заготовка для аналитика отправлены файлами выше.",
        reply_markup=MENU_KB,
    )
    try:
        await wait_msg.delete()
    except Exception as e:
        logger.debug("socrates final wait_msg.delete suppressed: %s", e)


# ── Small helpers ─────────────────────────────────────────────


async def _reject_if_too_long(duration_sec: float, wait_msg: Message) -> None:
    duration_min = duration_sec / 60
    if duration_min > settings.meeting_max_minutes:
        await _abort(
            wait_msg,
            f"❌ Запись длиннее {settings.meeting_max_minutes} мин "
            f"({duration_min:.1f} мин). В текущей итерации такие длинные "
            f"встречи не обрабатываются.",
        )


async def _safe_edit(wait_msg: Message, text: str) -> None:
    """Edit the wait message, swallowing transient Telegram errors.

    Rate-limit and "message not modified" happen routinely during long
    pipelines; we log them at debug and keep going.
    """
    try:
        await wait_msg.edit_text(text)
    except TelegramRetryAfter as e:
        logger.debug("socrates edit rate-limited: retry_after=%ss", e.retry_after)
    except Exception as e:
        logger.debug("socrates edit suppressed: %s", e)


async def _abort(wait_msg: Message, user_message: str) -> NoReturn:
    """Send a final error message and stop the pipeline.

    Raises _StageAbort so the caller's try/finally runs cleanup but
    the remaining stages are skipped. Typed NoReturn so callers
    (and type-checkers) understand this never falls through.
    """
    try:
        await wait_msg.edit_text(user_message, reply_markup=MENU_KB)
    except Exception as e:
        logger.debug("socrates abort edit suppressed: %s", e)
    raise _StageAbort


def _source_name_from_url(url: str) -> str:
    # Keep it short and safe — file-system basename without query string.
    # For Yandex.Disk links the tail is an opaque hash (`XYZ123abc`), which
    # looks ugly in the transcript header; collapse those to a readable label.
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}:
        return "Yandex.Disk"
    tail = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return tail or "meeting"
