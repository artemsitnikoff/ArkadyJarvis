"""AI client — uses Claude CLI (subscription, no API tokens)."""

import asyncio
import os

from app.config import settings


class AIClient:
    """Claude CLI wrapper. Uses subscription via CLAUDE_CODE_OAUTH_TOKEN."""

    async def complete(self, prompt: str, timeout: int = 120) -> str:
        return await self._call_cli(prompt, timeout=timeout)

    async def _call_cli(self, prompt: str, timeout: int = 120) -> str:
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
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=prompt.encode()), timeout=timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"claude CLI не ответил за {timeout}с")
        if proc.returncode != 0:
            err = stderr.decode().strip()[:300] or stdout.decode().strip()[:300]
            raise RuntimeError(f"claude CLI (code {proc.returncode}): {err}")
        result = stdout.decode().strip()
        if not result:
            raise RuntimeError("claude CLI вернул пустой ответ")
        return result

    async def close(self):
        pass  # no persistent client to close
