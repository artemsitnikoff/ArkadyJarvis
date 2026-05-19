import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from app.config import settings
from app.services.prompts import load_prompt
from app.utils import parse_json_response

logger = logging.getLogger("arkadyjarvis")

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


@dataclass
class TranscriptionResult:
    success: bool
    error: str = ""
    speakers_count: int = 0
    segments: list[dict] = field(default_factory=list)
    full_text: str = ""
    retryable: bool = False


def _format_time(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _explain_empty_content(finish_reason: str | None, refusal: str | None) -> str:
    """Translate an empty-content response into a short human-facing hint."""
    if refusal:
        return f" (модель отказала: {refusal[:120]})"
    if finish_reason == "content_filter":
        return " (сработал контент-фильтр — попробуй другую запись)"
    if finish_reason == "length":
        return " (ответ обрезан по лимиту токенов — запись слишком длинная)"
    if finish_reason == "stop":
        return " (модель завершила вывод с пустым ответом — возможно, в записи не распознана речь)"
    return f" (finish_reason={finish_reason!r})"


def _build_full_text(segments: list[dict]) -> str:
    parts = []
    for seg in segments:
        speaker = seg.get("speaker", "S?")
        start = float(seg.get("start", 0))
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        parts.append(f"{speaker} [{_format_time(start)}]: {text}")
    return "\n\n".join(parts)


class OpenRouterClient:
    """OpenRouter client for image generation."""

    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=10.0,
                read=settings.openrouter_timeout,
                write=settings.openrouter_timeout,
                pool=10.0,
            ),
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )

    async def close(self):
        await self._client.aclose()

    async def complete_text(
        self,
        prompt: str,
        model: str = "anthropic/claude-haiku-4.5",
        json_mode: bool = False,
        timeout: float = 60.0,
    ) -> str:
        """Text completion через OpenRouter (для дешёвых классификаторов на Haiku,
        чтобы не жечь Claude-subscription квоту опуса).

        `model` — OpenRouter-стиль ID: 'anthropic/claude-haiku-4.5',
        'anthropic/claude-sonnet-4.5' и т.п.
        `json_mode=True` — просим JSON response.
        """
        payload: dict = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        resp = await self._client.post(BASE_URL, json=payload, timeout=timeout)
        if resp.status_code >= 400:
            body = resp.text[:300]
            logger.error("OpenRouter complete_text %d: %s", resp.status_code, body)
            raise ValueError(f"OpenRouter {resp.status_code}: {body}")
        data = resp.json()
        choices = data.get("choices") or []
        if not choices:
            err = data.get("error", {}).get("message", "no choices")
            raise ValueError(f"OpenRouter: {err}")
        msg = choices[0].get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        return (content or "").strip()

    async def generate_image(self, prompt: str, image_b64: str | None = None) -> bytes:
        """Generate an image via Gemini through OpenRouter. Returns raw PNG bytes.

        If image_b64 is provided, sends it alongside the prompt (edit/transform mode).
        """
        if image_b64:
            content = [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}"}},
            ]
        else:
            content = prompt

        payload = {
            "model": "google/gemini-3-pro-image-preview",
            "messages": [{"role": "user", "content": content}],
            "modalities": ["image", "text"],
        }
        resp = await self._client.post(BASE_URL, json=payload)
        if resp.status_code >= 400:
            try:
                err = resp.json()
                msg = err.get("error", {}).get("message", "") or resp.text[:200]
            except Exception:
                msg = resp.text[:200]
            logger.error("OpenRouter HTTP %d: %s", resp.status_code, msg)
            raise ValueError(f"OpenRouter {resp.status_code}: {msg}")
        data = resp.json()

        for choice in data.get("choices", []):
            message = choice.get("message", {})

            # 1) Separate "images" array (OpenRouter documented format)
            for img in message.get("images", []):
                if img.get("type") == "image_url":
                    url = img["image_url"]["url"]
                    if url.startswith("data:"):
                        b64 = url.split(",", 1)[1]
                        return base64.b64decode(b64)

            content = message.get("content")

            # 2) String with inline base64 data URI
            if isinstance(content, str):
                m = re.search(r"data:image/[^;]+;base64,([A-Za-z0-9+/=]+)", content)
                if m:
                    return base64.b64decode(m.group(1))

            # 3) Content array (multimodal)
            if isinstance(content, list):
                for part in content:
                    if part.get("type") == "image_url":
                        url = part["image_url"]["url"]
                        if url.startswith("data:"):
                            b64 = url.split(",", 1)[1]
                            return base64.b64decode(b64)
                    if part.get("type") == "image":
                        b64 = part.get("data") or part.get("base64", "")
                        if b64:
                            return base64.b64decode(b64)

        # Extract text reason from response (e.g. content policy refusal)
        text_reason = ""
        for choice in data.get("choices", []):
            message = choice.get("message", {})
            content = message.get("content")
            if isinstance(content, str) and content.strip():
                text_reason = content.strip()
                break
            if isinstance(content, list):
                parts = [p.get("text", "") for p in content if p.get("type") == "text"]
                if parts:
                    text_reason = " ".join(parts).strip()
                    break

        snippet = json.dumps(data, ensure_ascii=False)[:1000]
        logger.error("No image in response: %s", snippet)

        if text_reason:
            raise ValueError(text_reason)

        # Gemini silently refuses: empty content + 0 completion tokens = content policy
        usage = data.get("usage", {})
        if usage.get("completion_tokens", -1) == 0:
            raise ValueError("Модель отказала — возможно, запрос нарушает контент-политику")

        raise ValueError("Модель не вернула картинку, попробуй другой промпт")

    async def transcribe_voice(
        self, audio_path: str | Path, audio_format: str = "ogg",
    ) -> TranscriptionResult:
        """Transcribe an audio file with speaker diarization.

        `audio_format` — formats supported by Gemini: ogg, mp3, wav, aac, flac, aiff.
        """
        path = Path(audio_path)
        try:
            audio_b64 = await asyncio.to_thread(
                lambda: base64.b64encode(path.read_bytes()).decode()
            )
        except Exception as e:
            return TranscriptionResult(success=False, error=f"не смог прочитать файл: {e}")

        for attempt in range(2):
            result = await self._transcribe_once(audio_b64, audio_format)
            if result.success or not result.retryable:
                return result
            logger.warning("Transcribe retry %d/2 after retryable error: %s", attempt + 1, result.error)
            await asyncio.sleep(5)
        return result

    async def _transcribe_once(self, audio_b64: str, audio_format: str = "ogg") -> TranscriptionResult:
        prompt = load_prompt("voice_transcribe")
        payload = {
            "model": settings.openrouter_model,
            "messages": [{
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": audio_format}},
                ],
            }],
            "response_format": {"type": "json_object"},
            "max_tokens": 60000,
        }

        try:
            resp = await self._client.post(BASE_URL, json=payload)
        except Exception as e:
            logger.error("Transcribe HTTP error: %s", e, exc_info=True)
            return TranscriptionResult(success=False, error=str(e), retryable=True)

        if resp.status_code >= 400:
            body = resp.text[:300]
            logger.error("Transcribe %d: %s", resp.status_code, body)
            return TranscriptionResult(
                success=False,
                error=f"OpenRouter {resp.status_code}: {body}",
                retryable=resp.status_code == 429 or 500 <= resp.status_code < 600,
            )

        # Hoist finish_reason + content out of the try-block so error logging
        # doesn't rely on locals().get(...).
        finish_reason: str = "?"
        content: str | None = None
        refusal = None
        try:
            data = resp.json()
        except Exception as e:
            logger.error("Transcribe: failed to parse JSON: %s | body=%r", e, resp.text[:500])
            return TranscriptionResult(
                success=False, error=f"невалидный JSON от OpenRouter: {e}", retryable=True,
            )

        # Top-level error (no choices at all — different from in-choice error)
        top_error = data.get("error")
        if top_error and not data.get("choices"):
            err_type = (top_error.get("metadata") or {}).get("error_type", "")
            err_code = top_error.get("code", "")
            err_msg = top_error.get("message", "")
            retryable = (
                err_type in {"provider_overloaded", "rate_limit", "timeout"}
                or (isinstance(err_code, int) and (err_code == 429 or 500 <= err_code < 600))
            )
            logger.error(
                "Transcribe top-level error: code=%s type=%s msg=%s retryable=%s",
                err_code, err_type, err_msg, retryable,
            )
            return TranscriptionResult(
                success=False,
                error=f"OpenRouter: {err_msg or err_type or err_code}",
                retryable=retryable,
            )

        if not data.get("choices"):
            logger.error("Transcribe: no choices in response | raw=%s", str(data)[:500])
            return TranscriptionResult(
                success=False, error="OpenRouter не вернул choices", retryable=True,
            )

        try:
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason", "?")
            message = choice.get("message") or {}
            content = message.get("content")
            refusal = message.get("refusal")
            if isinstance(content, list):
                content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
            logger.info(
                "Transcribe finish_reason=%s content_len=%s usage=%s refusal=%r",
                finish_reason,
                len(content) if isinstance(content, str) else "n/a",
                data.get("usage"),
                refusal,
            )

            # OpenRouter sometimes returns HTTP 200 with the upstream error
            # embedded in the choice payload (provider_overloaded etc.).
            choice_error = choice.get("error") or data.get("error")
            if choice_error and (not isinstance(content, str) or not content.strip()):
                err_type = (choice_error.get("metadata") or {}).get("error_type", "")
                err_code = choice_error.get("code", "")
                err_msg = choice_error.get("message", "")
                retryable = (
                err_type in {"provider_overloaded", "rate_limit", "timeout"}
                or (isinstance(err_code, int) and (err_code == 429 or 500 <= err_code < 600))
            )
                logger.error(
                    "Transcribe upstream error: code=%s type=%s msg=%s retryable=%s",
                    err_code, err_type, err_msg, retryable,
                )
                if retryable:
                    return TranscriptionResult(
                        success=False,
                        error=f"провайдер перегружен ({err_type or err_code}) — попробуй ещё раз",
                        retryable=True,
                    )
                return TranscriptionResult(
                    success=False,
                    error=f"upstream error: {err_msg or err_type or err_code}",
                )

            if not isinstance(content, str) or not content.strip():
                logger.error(
                    "Transcribe got empty content: finish_reason=%s refusal=%r "
                    "usage=%s raw=%s",
                    finish_reason, refusal, data.get("usage"), str(data)[:500],
                )
                reason_hint = _explain_empty_content(finish_reason, refusal)
                return TranscriptionResult(
                    success=False,
                    error=f"модель не вернула текст{reason_hint}",
                )

            parsed = parse_json_response(content)
        except Exception as e:
            # Dump enough of the raw content to diagnose truncation vs. malformed JSON.
            head = (content[:500] if isinstance(content, str) else str(content)[:500])
            tail = (content[-500:] if isinstance(content, str) and len(content) > 500 else "")
            logger.error(
                "Transcribe parse error: %s | finish_reason=%s | content_len=%s | "
                "head=%r | tail=%r",
                e,
                finish_reason,
                len(content) if isinstance(content, str) else "n/a",
                head, tail,
                exc_info=True,
            )
            hint = ""
            if finish_reason == "length":
                hint = " (ответ обрезан по лимиту токенов — попробуй более короткую запись)"
            return TranscriptionResult(
                success=False, error=f"не смог разобрать ответ: {e}{hint}",
            )

        segments = parsed.get("segments") or []
        # Sanity: ensure monotonic timestamps and end >= start
        for seg in segments:
            seg["start"] = max(0.0, float(seg.get("start", 0) or 0))
            seg["end"] = max(seg["start"], float(seg.get("end", seg["start"]) or seg["start"]))

        full_text = _build_full_text(segments)
        if not full_text:
            return TranscriptionResult(
                success=False, error="пустая расшифровка — возможно, тишина или слишком тихо",
            )

        return TranscriptionResult(
            success=True,
            speakers_count=int(parsed.get("speakers_count") or 0),
            segments=segments,
            full_text=full_text,
        )
