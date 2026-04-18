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


async def probe_duration(path: str | Path) -> float:
    """Return duration of a media file in seconds. Raises FFmpegError."""
    path = str(path)
    # ffprobe is shipped alongside ffmpeg; we use the same bin directory.
    ffprobe = settings.ffmpeg_bin.replace("ffmpeg", "ffprobe") or "ffprobe"
    proc = await asyncio.create_subprocess_exec(
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json",
        path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffprobe failed: {stderr.decode()[:300]}")
    try:
        data = json.loads(stdout.decode())
        return float(data["format"]["duration"])
    except (KeyError, ValueError, json.JSONDecodeError) as e:
        raise FFmpegError(f"ffprobe: unable to parse duration: {e}") from e


async def convert_to_opus(input_path: str | Path, output_path: str | Path) -> None:
    """Convert video / audio to mono 16 kHz opus @ 24 kbps.

    Drops any video stream (`-vn`). Overwrites output (`-y`).
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
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise FFmpegError(f"ffmpeg failed: {stderr.decode()[-500:]}")
    logger.info("ffmpeg: %s -> %s OK", input_path, output_path)
