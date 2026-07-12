"""Meeting transcription + review + expertise pipeline (Socrates).

Stage 0 (ffmpeg) is done by the caller before invoking this pipeline, so
here we start from a compact `.ogg` file plus a human-readable `source_name`
and a pre-computed `duration_sec` from ffprobe.
"""

import inspect
import logging
import math
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from app.services.ffmpeg_tool import FFmpegError, split_audio
from app.services.openrouter_client import TranscriptionResult, _build_full_text
from app.services.prompts import load_prompt

logger = logging.getLogger("arkadyjarvis")

# Длинную запись Gemini/OpenRouter не проглатывает одним запросом
# (provider_overloaded / timeout). Записи длиннее порога режем на куски и
# транскрибируем по частям — каждый запрос маленький и быстрый.
_CHUNK_SECONDS = 20 * 60          # куски по 20 мин
_CHUNK_IF_LONGER_THAN = 22 * 60   # ≤22 мин — шлём одним запросом (как раньше)
_OPUS_BYTES_PER_SEC = 3000        # 24 кбит/с ≈ 3000 Б/с — оценка длит. по размеру


@dataclass
class MeetingArtifacts:
    transcript_md: str
    review_md: str
    brief_md: str


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 60}:{total % 60:02d}"


def _build_transcript_md(
    source_name: str,
    duration_sec: float,
    speakers_count: int,
    full_text: str,
) -> str:
    """Wrap the diarized transcript into the markdown shape from the spec."""
    return (
        f"# Расшифровка встречи\n\n"
        f"**Источник:** {source_name}\n"
        f"**Длительность:** {_format_duration(duration_sec)}\n"
        f"**Обнаружено спикеров:** {speakers_count}\n\n"
        f"---\n\n"
        f"{full_text}\n"
    )


ProgressCallback = Callable[[str], Awaitable[None] | None] | None


async def _transcribe(
    ogg_path: Path, *, openrouter, duration_sec: float, tick,
) -> TranscriptionResult:
    """Транскрипция с авто-нарезкой длинных записей.

    Короткая (≤ порога) — одним запросом, как раньше. Длинная — режем на куски
    по `_CHUNK_SECONDS`, транскрибируем последовательно (последовательно, чтобы
    не ловить provider_overloaded из-за параллельных запросов), склеиваем
    сегменты со сквозными таймкодами (смещаем на `i * _CHUNK_SECONDS`)."""
    # Оценка длительности: probe, иначе по размеру опуса.
    est = duration_sec
    if est <= 0:
        try:
            est = ogg_path.stat().st_size / _OPUS_BYTES_PER_SEC
        except OSError:
            est = 0.0

    if est <= 0 or est <= _CHUNK_IF_LONGER_THAN:
        return await openrouter.transcribe_voice(str(ogg_path))

    n = math.ceil(est / _CHUNK_SECONDS)
    await tick(
        f"Запись длинная (~{est / 60:.0f} мин) — режу на {n} частей "
        "и транскрибирую по частям..."
    )
    try:
        chunks = await split_audio(ogg_path, ogg_path.parent / "chunks", _CHUNK_SECONDS)
    except FFmpegError as e:
        logger.warning("split_audio failed (%s) — fallback на один запрос", e)
        return await openrouter.transcribe_voice(str(ogg_path))
    if not chunks:
        return await openrouter.transcribe_voice(str(ogg_path))

    merged: list[dict] = []
    max_speakers = 0
    for i, chunk in enumerate(chunks):
        await tick(f"Транскрибирую часть {i + 1}/{len(chunks)}...")
        res = await openrouter.transcribe_voice(str(chunk))
        if not res.success:
            return TranscriptionResult(
                success=False, error=f"часть {i + 1}/{len(chunks)}: {res.error}",
            )
        offset = i * _CHUNK_SECONDS
        for seg in res.segments:
            seg = dict(seg)
            seg["start"] = float(seg.get("start", 0) or 0) + offset
            seg["end"] = float(seg.get("end", seg["start"]) or seg["start"]) + offset
            merged.append(seg)
        max_speakers = max(max_speakers, res.speakers_count)

    full_text = _build_full_text(merged)
    if not full_text:
        return TranscriptionResult(
            success=False, error="пустая расшифровка после склейки частей",
        )
    return TranscriptionResult(
        success=True, speakers_count=max_speakers, segments=merged, full_text=full_text,
    )


async def process_meeting(
    ogg_path: str | Path,
    *,
    openrouter,
    ai_client,
    source_name: str,
    duration_sec: float,
    on_progress: ProgressCallback = None,
) -> MeetingArtifacts:
    """Run stages 1-3 against a pre-compressed .ogg file.

    Raises on transcription / AI failures so the caller can surface a
    concrete error to the user.
    """
    async def _tick(msg: str):
        logger.info("meeting_pipeline: %s", msg)
        if on_progress:
            try:
                res = on_progress(msg)
                if inspect.isawaitable(res):
                    await res
            except Exception as e:
                logger.warning("progress callback failed: %s", e)

    # ── Stage 1: transcribe (с авто-нарезкой длинных записей) ────
    await _tick("Транскрибирую запись (диаризация)...")
    result = await _transcribe(
        Path(ogg_path), openrouter=openrouter, duration_sec=duration_sec, tick=_tick,
    )
    if not result.success:
        raise RuntimeError(f"Транскрипция не удалась: {result.error}")

    transcript_md = _build_transcript_md(
        source_name=source_name,
        duration_sec=duration_sec,
        speakers_count=result.speakers_count,
        full_text=result.full_text,
    )

    # ── Stage 2: meeting review ────────────────────────────────
    await _tick("Готовлю ревью встречи...")
    review_prompt = load_prompt("meeting_review")
    review_md = await ai_client.complete(
        f"{review_prompt}\n\n---\n\n{transcript_md}",
        timeout=600,
    )

    # ── Stage 3: analyst brief (zero-stage prep) ───────────────
    await _tick("Готовлю заготовку для аналитика...")
    brief_prompt = load_prompt("meeting_brief")
    brief_input = (
        f"{brief_prompt}\n\n---\n\n"
        f"# Транскрипт встречи\n\n{transcript_md}\n\n---\n\n"
        f"# Ревью встречи\n\n{review_md}"
    )
    brief_md = await ai_client.complete(brief_input, timeout=600)

    return MeetingArtifacts(
        transcript_md=transcript_md,
        review_md=review_md,
        brief_md=brief_md,
    )
