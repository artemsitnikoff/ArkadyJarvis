import logging

from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class AIClient:
    """Singleton OpenAI client wrapper."""

    _instance: "AIClient | None" = None

    def __init__(self):
        self._client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())

    @classmethod
    def get(cls) -> "AIClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

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

    @property
    def raw(self) -> AsyncOpenAI:
        return self._client
