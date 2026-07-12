"""ffmpeg wrappers for meeting audio pre-processing.

- probe_duration: fast media duration lookup via ffprobe
- convert_to_opus: mono 16 kHz opus @ 24 kbps (speech-STT optimised)
"""

import asyncio
import json
import logging
from pathlib import Path

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class FFmpegError(RuntimeError):
    pass


def _ffprobe_path() -> str:
    """Return the ffprobe binary that lives next to the configured ffmpeg.

    Using `Path.with_name("ffprobe")` correctly handles both bare `"ffmpeg"`
    (returns `"ffprobe"`) and absolute paths like `/opt/ffmpeg/bin/ffmpeg`
    (returns `/opt/ffmpeg/bin/ffprobe`). String `.replace()` would mangle
    the directory component in the latter case.
    """
    bin_ = settings.ffmpeg_bin
    if not bin_ or bin_ == "ffmpeg":
        return "ffprobe"
    return str(Path(bin_).with_name("ffprobe"))


async def probe_duration(path: str | Path) -> float:
    """Return duration of a media file in seconds. Raises FFmpegError."""
    path = str(path)
    ffprobe = _ffprobe_path()
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError("ffprobe не уложился в 60с") from None
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {stderr.decode(errors='replace')[:300]}")
    try:
        data = json.loads(stdout.decode(errors="replace"))
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise FFmpegError(f"ffprobe: unable to parse duration: {e}") from e


FFMPEG_TIMEOUT_SEC = 1800  # 30 min — covers 2 GB inputs on modest hardware


async def convert_to_opus(input_path: str | Path, output_path: str | Path) -> None:
    """Convert video / audio to mono 16 kHz opus @ 24 kbps.

    Drops any video stream (`-vn`). Overwrites output (`-y`). Bounded
    by FFMPEG_TIMEOUT_SEC; the subprocess is killed on timeout so we
    don't accumulate zombie ffmpeg instances on pathological inputs.
    """
    input_path = str(input_path)
    output_path = str(output_path)
    args = [
        settings.ffmpeg_bin,
        "-y",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "libopus",
        "-b:a", "24k",
        output_path,
    ]
    logger.info("ffmpeg argv: %s", args)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(
            f"ffmpeg не уложился в {FFMPEG_TIMEOUT_SEC}с — процесс убит",
        ) from None
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg failed: {stderr.decode(errors='replace')[-500:]}"
        )
    logger.info("ffmpeg: %s -> %s OK", input_path, output_path)


async def split_audio(
    input_path: str | Path, out_dir: str | Path, segment_seconds: int,
) -> list[Path]:
    """Режет аудио на куски по `segment_seconds` (stream copy, без перекодирования).

    Нужно для чанковой транскрипции: Gemini/OpenRouter не тянет длинную запись
    одним запросом (provider_overloaded/timeout), поэтому режем на части и
    транскрибируем по кускам. Вход уже наш opus/ogg — потому `-c:a copy`
    (быстро, без потерь). Возвращает пути кусков по порядку (chunk_000.ogg …).
    """
    input_path = str(input_path)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_pattern = str(out_dir / "chunk_%03d.ogg")
    args = [
        settings.ffmpeg_bin,
        "-y",
        "-i", input_path,
        "-vn",
        "-c:a", "copy",
        "-f", "segment",
        "-segment_time", str(int(segment_seconds)),
        out_pattern,
    ]
    logger.info("ffmpeg split argv: %s", args)
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        _, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=FFMPEG_TIMEOUT_SEC,
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise FFmpegError(
            f"ffmpeg split не уложился в {FFMPEG_TIMEOUT_SEC}с — процесс убит",
        ) from None
    if proc.returncode != 0:
        raise FFmpegError(
            f"ffmpeg split failed: {stderr.decode(errors='replace')[-500:]}"
        )
    chunks = sorted(out_dir.glob("chunk_*.ogg"))
    logger.info("ffmpeg split: %s -> %d кусков", input_path, len(chunks))
    return chunks
