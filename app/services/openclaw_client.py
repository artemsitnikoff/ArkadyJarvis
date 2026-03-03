import json
import logging
from collections.abc import AsyncIterator

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class OpenClawClient:
    """Client for OpenClaw gateway (SSE streaming, OpenAI-compatible API)."""

    def __init__(self):
        token = settings.openclaw_token.get_secret_value()
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10.0, read=180.0, write=10.0, pool=10.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        self._base_url = settings.openclaw_url.rstrip("/")

    async def close(self):
        await self._client.aclose()

    async def stream_chat(
        self, messages: list[dict[str, str]],
    ) -> AsyncIterator[str]:
        """Send messages to OpenClaw and yield text chunks via SSE.

        Args:
            messages: OpenAI-format message list [{"role": "user", "content": "..."}]

        Yields:
            Text delta chunks from the assistant response.
        """
        if not self._base_url:
            raise ValueError("OPENCLAW_URL not configured")

        url = f"{self._base_url}/v1/chat/completions"
        payload = {
            "model": "openclaw",
            "stream": True,
            "messages": messages,
        }
        headers = {"x-openclaw-agent-id": settings.openclaw_agent_id}

        async with self._client.stream(
            "POST", url, json=payload, headers=headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:]
                if data_str.strip() == "[DONE]":
                    break
                try:
                    chunk = json.loads(data_str)
                    delta = chunk["choices"][0].get("delta", {})
                    content = delta.get("content")
                    if content:
                        yield content
                except (json.JSONDecodeError, KeyError, IndexError):
                    continue

    async def chat(self, messages: list[dict[str, str]]) -> str:
        """Non-streaming convenience method. Returns full response text."""
        parts: list[str] = []
        async for chunk in self.stream_chat(messages):
            parts.append(chunk)
        return "".join(parts)
