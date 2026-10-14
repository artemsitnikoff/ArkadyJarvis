"""Socrates — meeting analyser.

User flow:
  1. Press "🎓 Сократ" → enter FSM waiting_for_url.
  2. Send a URL (Yandex.Disk / Google Drive / direct download URL — видео ИЛИ
     аудио, mp3 в том числе) или аудиофайл прямо в чат (mp3/m4a/ogg/voice,
     ≤ 20 MB — это лимит Telegram Bot API на скачивание ботом).
  3. Bot downloads → ffmpeg (-vn, любой контейнер → mono 16 kHz opus) →
     transcribe → review → brief. Формат источника пайплайну безразличен.
  4. Three .md files are attached to the chat.

Видео крупнее 20 MB напрямую не принимается — только ссылкой.

Open to every authorised user (no allow-list). Per-user concurrency
lock prevents the same user from stacking pipelines.
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


# Telegram Bot API отдаёт ботам файлы только до 20 MB (getFile). Крупнее — только ссылкой.
TELEGRAM_BOT_FILE_LIMIT = 20 * 1024 * 1024

_HINT_SOURCES = (
    "Пришли ссылку на запись (Яндекс.Диск / Google Drive / прямой URL — видео или "
    "mp3) либо аудиофайл прямо сюда (mp3/m4a/ogg/голосовое до 20 МБ)."
)


@router.message(Socrates.waiting_for_url, F.text)
async def handle_meeting_url(
    message: Message, state: FSMContext, openrouter, ai_client,
):
    url = (message.text or "").strip()
    if not URL_RE.match(url):
        await message.reply(_HINT_SOURCES)
        return
    logger.info("*** SOCRATES: url=%s from user=%s", url[:120], message.from_user.id)

    async def _fetch(dest: Path) -> int:
        return await download_meeting(url, dest)

    await _run(message, state, openrouter, ai_client,
               source_name=_source_name_from_url(url), fetch=_fetch)


@router.message(
    Socrates.waiting_for_url,
    F.audio | F.voice | F.video | F.video_note | F.document,
)
async def handle_meeting_file(
    message: Message, state: FSMContext, openrouter, ai_client,
):
    """Аудио/видео присланное файлом (≤ 20 MB). mp3 через Telegram-«музыку» (F.audio),
    голосовое (F.voice), документ с audio/* или video/* mime."""
    media = (
        message.audio or message.voice or message.video
        or message.video_note or message.document
    )
    mime = (getattr(media, "mime_type", None) or "").lower()
    if message.document and not mime.startswith(("audio/", "video/")):
        await message.reply("Это не аудио и не видео. " + _HINT_SOURCES)
        return
    size = getattr(media, "file_size", None) or 0
    if size > TELEGRAM_BOT_FILE_LIMIT:
        await message.reply(
            f"Файл {size / (1024 * 1024):.0f} МБ — Telegram отдаёт ботам файлы только "
            "до 20 МБ. Залей на Яндекс.Диск / Google Drive и пришли ссылку.",
        )
        return
    source_name = (
        getattr(media, "file_name", None)
        or ("voice.ogg" if message.voice else f"telegram_{media.file_unique_id}")
    )
    logger.info(
        "*** SOCRATES: file=%s mime=%s size=%s from user=%s",
        source_name, mime or "?", size, message.from_user.id,
    )

    async def _fetch(dest: Path) -> int:
        await message.bot.download(media, destination=str(dest))
        return dest.stat().st_size

    await _run(message, state, openrouter, ai_client, source_name=source_name, fetch=_fetch)


@router.message(Socrates.waiting_for_url)
async def handle_meeting_other(message: Message):
    await message.reply(_HINT_SOURCES)


async def _run(
    message: Message, state: FSMContext, openrouter, ai_client, *,
    source_name: str, fetch,
) -> None:
    """Общий прогон для ссылки и для файла. `fetch(dest) -> bytes` кладёт источник
    в dest (скачивание по URL либо bot.download)."""
    user_lock = await _get_user_lock(message.from_user.id)
    if user_lock.locked():
        await state.clear()
        await message.reply(
            "⏳ Уже обрабатываю твою предыдущую запись. Дождись окончания "
            "и пришли следующую.",
            reply_markup=MENU_KB,
        )
        return

    await state.clear()
    async with user_lock:
        wait_msg = await message.reply("📥 Скачиваю запись...")
        tmpdir = Path(tempfile.mkdtemp(prefix="socrates_"))
        raw_path = tmpdir / "source.bin"
        ogg_path = tmpdir / "audio.ogg"

        try:
            size_mb = await _download_stage(fetch, raw_path, wait_msg)
            duration_sec = await _convert_and_probe_stage(
                raw_path, ogg_path, size_mb, wait_msg,
            )
            artifacts = await _run_pipeline_stage(
                ogg_path, duration_sec, source_name, openrouter, ai_client, wait_msg,
            )
            await _deliver_artifacts(message, wait_msg, artifacts)
        except _StageAbort:
            # User already notified by the stage helper.
            return
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)


# ── Stage helpers ─────────────────────────────────────────────


async def _download_stage(fetch, raw_path: Path, wait_msg: Message) -> float:
    """Stage 0a. `fetch(dest) -> bytes`. Returns downloaded size in MiB. Aborts on failure."""
    try:
        size_bytes = await fetch(raw_path)
    except DownloadError as e:
        await _abort(wait_msg, f"❌ Не смог скачать: {e}")
    except Exception as e:
        # bot.download: «file is too big» и прочие TelegramBadRequest — без внутренностей.
        logger.error("socrates fetch failed: %s", e, exc_info=True)
        await _abort(wait_msg, "❌ Не смог получить файл. Попробуй ссылкой на Яндекс.Диск / Google Drive.")
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

    duration_min = 0.0  # остаётся 0, если оба probe упали (раньше — UnboundLocalError ниже)
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
    ogg_path: Path, duration_sec: float, source_name: str, openrouter, ai_client,
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
            source_name=source_name,
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
    # For Yandex.Disk / Google Drive links the tail is an opaque hash, which
    # looks ugly in the transcript header; collapse those to a readable label.
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    if host in {"disk.yandex.ru", "disk.yandex.com", "yadi.sk"}:
        return "Yandex.Disk"
    if host in {"drive.google.com", "docs.google.com", "drive.usercontent.google.com"}:
        return "Google Drive"
    tail = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return tail or "meeting"
