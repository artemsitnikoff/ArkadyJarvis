"""Meeting transcription + review + expertise pipeline (Socrates).

Stage 0 (ffmpeg) is done by the caller before invoking this pipeline, so
here we start from a compact `.ogg` file plus a human-readable `source_name`
and a pre-computed `duration_sec` from ffprobe.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.services.prompts import load_prompt

logger = logging.getLogger("arkadyjarvis")


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


ProgressCallback = Callable[[str], None] | None


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
                if hasattr(res, "__await__"):
                    await res
            except Exception as e:
                logger.warning("progress callback failed: %s", e)

    # ── Stage 1: transcribe ─────────────────────────────────────
    await _tick("Транскрибирую запись (диаризация)...")
    result = await openrouter.transcribe_voice(str(ogg_path))
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
