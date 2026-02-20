import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class AIClient:
    """OpenAI client wrapper."""

    def __init__(self):
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())

    async def complete(
        self, prompt: str, max_tokens: int = 1024, temperature: float = 1.0
    ) -> str:
        response = await self._client.chat.completions.create(
            model=settings.openai_model,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.choices[0].message.content.strip()

    async def chat(
        self, messages: list[dict], max_tokens: int = 1024, temperature: float = 0.9
    ) -> str:
        response = await self._client.chat.completions.create(
            model=settings.openai_model,
            max_completion_tokens=max_tokens,
            temperature=temperature,
            messages=messages,
        )
        return response.choices[0].message.content.strip()

    async def close(self):
        await self._client.close()

    @property
    def raw(self) -> AsyncOpenAI:
        return self._client
