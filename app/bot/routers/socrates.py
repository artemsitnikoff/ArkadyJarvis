"""Socrates — meeting analyser.

User flow:
  1. Press "🎓 Сократ" → enter FSM waiting_for_url.
  2. Send a URL (Yandex.Disk public link or direct download URL).
  3. Bot downloads → ffmpeg → transcribe → review → expertise.
  4. Three .md files are attached to the chat.

Telegram Bot API caps uploads at 20 MB, so direct file uploads are not
supported — the recording MUST be shared via a link.
"""

import logging
import re
import tempfile
from pathlib import Path

from aiogram import F, Router
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


class Socrates(StatesGroup):
    waiting_for_url = State()


@router.message(Socrates.waiting_for_url, F.text)
async def handle_meeting_url(
    message: Message, state: FSMContext, openrouter, ai_client,
):
    url = (message.text or "").strip()
    if not URL_RE.match(url):
        await message.reply(
            "Пришли ссылку на запись (http(s)://...). "
            "Yandex.Диск / Telemost или прямой URL."
        )
        return

    await state.clear()
    logger.info("*** SOCRATES: url=%s from user=%s", url[:120], message.from_user.id)

    wait_msg = await message.reply("📥 Скачиваю запись...")

    # All temp artifacts live inside a single directory so cleanup is atomic.
    tmpdir = Path(tempfile.mkdtemp(prefix="socrates_"))
    raw_path = tmpdir / "source.bin"
    ogg_path = tmpdir / "audio.ogg"

    try:
        # ── Stage 0a: download ───────────────────────────────────
        try:
            size_bytes = await download_meeting(url, raw_path)
        except DownloadError as e:
            await wait_msg.edit_text(f"❌ Не смог скачать: {e}", reply_markup=MENU_KB)
            return

        size_mb = size_bytes / (1024 * 1024)
        await wait_msg.edit_text(
            f"🎚 Скачано {size_mb:.1f} МБ. Проверяю длительность...",
        )

        # ── Stage 0b: reject too-long recordings BEFORE ffmpeg ──
        # Probe the original file so we don't waste CPU on converting a
        # 10-hour video just to reject it afterwards.
        try:
            duration_sec = await probe_duration(raw_path)
        except FFmpegError as e:
            logger.warning("probe_duration on raw file failed: %s — will retry on ogg", e)
            duration_sec = 0.0

        duration_min = duration_sec / 60
        if duration_sec > 0 and duration_min > settings.meeting_max_minutes:
            await wait_msg.edit_text(
                f"❌ Запись длиннее {settings.meeting_max_minutes} мин "
                f"({duration_min:.1f} мин). В текущей итерации такие длинные "
                f"встречи не обрабатываются.",
                reply_markup=MENU_KB,
            )
            return

        await wait_msg.edit_text(
            f"🎚 Скачано {size_mb:.1f} МБ, {duration_min:.1f} мин. "
            "Конвертирую аудио (ffmpeg)...",
        )

        # ── Stage 0c: ffmpeg ─────────────────────────────────────
        try:
            await convert_to_opus(raw_path, ogg_path)
        except FFmpegError as e:
            logger.error("ffmpeg failed: %s", e)
            await wait_msg.edit_text(
                f"❌ Не смог обработать аудио (ffmpeg): {e}",
                reply_markup=MENU_KB,
            )
            return

        # If the original probe failed, try again on the converted audio.
        if duration_sec == 0.0:
            try:
                duration_sec = await probe_duration(ogg_path)
                duration_min = duration_sec / 60
                if duration_min > settings.meeting_max_minutes:
                    await wait_msg.edit_text(
                        f"❌ Запись длиннее {settings.meeting_max_minutes} мин "
                        f"({duration_min:.1f} мин).",
                        reply_markup=MENU_KB,
                    )
                    return
            except FFmpegError:
                pass  # keep 0 — pipeline can still run

        ogg_size_mb = ogg_path.stat().st_size / (1024 * 1024)
        await wait_msg.edit_text(
            f"🎙 Аудио {ogg_size_mb:.1f} МБ, длительность {duration_min:.1f} мин.\n"
            "Транскрибирую запись (диаризация)...",
        )

        # ── Stages 1-3: transcript → review → expertise ──────────
        async def on_progress(msg: str):
            try:
                await wait_msg.edit_text(f"🧠 {msg}")
            except Exception as e:
                logger.debug("socrates on_progress suppressed: %s", e)

        try:
            artifacts = await process_meeting(
                ogg_path,
                openrouter=openrouter,
                ai_client=ai_client,
                source_name=_source_name_from_url(url),
                duration_sec=duration_sec,
                on_progress=on_progress,
            )
        except Exception as e:
            logger.error("meeting pipeline failed: %s", e, exc_info=True)
            await wait_msg.edit_text(
                f"❌ Пайплайн упал: {e}", reply_markup=MENU_KB,
            )
            return

        # ── Deliver artefacts as .md files ───────────────────────
        await wait_msg.edit_text("📎 Готово, отправляю артефакты...")

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
        await wait_msg.delete()
    finally:
        # Always wipe the working directory.
        _cleanup_dir(tmpdir)


def _source_name_from_url(url: str) -> str:
    # Keep it short and safe — file-system basename without query string.
    tail = url.split("?", 1)[0].rstrip("/").split("/")[-1]
    return tail or "meeting"


def _cleanup_dir(tmpdir: Path) -> None:
    try:
        for f in tmpdir.iterdir():
            try:
                f.unlink()
            except Exception as e:
                logger.warning("cleanup: failed to delete %s: %s", f, e)
        tmpdir.rmdir()
    except FileNotFoundError:
        pass
    except Exception as e:
        logger.warning("cleanup: failed to rmdir %s: %s", tmpdir, e)


@router.message(Socrates.waiting_for_url)
async def handle_meeting_not_text(message: Message):
    await message.reply(
        "Пришли ссылку на запись (текстовым сообщением). "
        "Файлы-видео напрямую не принимаю — Telegram режет upload на 20 МБ."
    )
