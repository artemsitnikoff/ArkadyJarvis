"""AI client — uses Claude CLI (subscription, no API tokens)."""

import asyncio
import logging
import os

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class AIClient:
    """Claude CLI wrapper. Uses subscription via CLAUDE_CODE_OAUTH_TOKEN."""

    async def complete(
        self, prompt: str, max_tokens: int = 4096, temperature: float = 1.0,
    ) -> str:
        return await self._call_cli(prompt)

    async def chat(
        self, messages: list[dict], max_tokens: int = 4096, temperature: float = 0.9,
    ) -> str:
        # Format messages as a conversation for CLI
        parts = []
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                parts.append(f"[Системная инструкция]: {content}")
            elif role == "assistant":
                parts.append(f"[Ассистент]: {content}")
            else:
                parts.append(f"[Пользователь]: {content}")
        prompt = "\n\n".join(parts)
        return await self._call_cli(prompt)

    async def _call_cli(self, prompt: str) -> str:
        from app.services.claude_token import ensure_fresh_token
        await ensure_fresh_token()

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        args = [settings.claude_cli_path, "--print", "--output-format", "text"]
        if settings.claude_model:
            args.extend(["--model", settings.claude_model])

        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=prompt.encode()), timeout=120,
        )
        if proc.returncode != 0:
            err = stderr.decode().strip()[:300] or stdout.decode().strip()[:300]
            raise RuntimeError(f"claude CLI (code {proc.returncode}): {err}")
        result = stdout.decode().strip()
        if not result:
            raise RuntimeError("claude CLI вернул пустой ответ")
        return result

    async def close(self):
        pass  # no persistent client to close
