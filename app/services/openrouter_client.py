import base64
import logging
import re

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

BASE_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    """Singleton OpenRouter client for image generation and Opus queries."""

    _instance: "OpenRouterClient | None" = None

    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=120,
            headers={
                "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                "Content-Type": "application/json",
            },
        )

    @classmethod
    def get(cls) -> "OpenRouterClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def close(self):
        await self._client.aclose()

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
        resp.raise_for_status()
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

        # Log full keys for debugging
        import json as _json
        snippet = _json.dumps(data, ensure_ascii=False)[:1000]
        logger.error("No image in response: %s", snippet)
        raise ValueError("No image data in OpenRouter response")

    async def ask_opus(self, prompt: str) -> str:
        """Ask Claude Opus 4.6 via OpenRouter. Returns text response."""
        payload = {
            "model": "anthropic/claude-opus-4.6",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 4096,
        }
        resp = await self._client.post(BASE_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

        content = data["choices"][0]["message"]["content"]
        if isinstance(content, str):
            return content.strip()
        # multimodal response — extract text parts
        parts = [p["text"] for p in content if p.get("type") == "text"]
        return "\n".join(parts).strip()
