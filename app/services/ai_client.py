"""AI client — uses Claude CLI (subscription, no API tokens)."""

import asyncio
import logging
import os

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class AIClient:
    """Claude CLI wrapper. Uses subscription via CLAUDE_CODE_OAUTH_TOKEN."""

    async def complete(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: str | None = None,
        allowed_tools: str | None = None,
        model: str | None = None,
    ) -> str:
        """Run Claude CLI with prompt.

        `allowed_tools`: comma-separated whitelist (e.g. "WebSearch,WebFetch").
        `model`: override settings.claude_model for this call only (e.g. "haiku"
        for cheap fast classification tasks).
        """
        return await self._call_cli(
            prompt,
            timeout=timeout,
            system_prompt=system_prompt,
            allowed_tools=allowed_tools,
            model=model,
        )

    # CRITICAL: disable all tools. Otherwise the CLI executes shell commands,
    # reads/writes files, fetches URLs etc. when the user prompt looks like
    # an instruction ("сделай cd ..., ls ..."). For our use case the CLI is
    # a stateless prompt→answer model, no agentic behaviour is wanted.
    DISALLOWED_TOOLS = (
        "Bash,BashOutput,KillShell,"
        "Read,Write,Edit,MultiEdit,NotebookEdit,"
        "Glob,Grep,"
        "WebFetch,WebSearch,"
        "Task,Agent,SlashCommand,TodoWrite,ExitPlanMode"
    )

    async def _call_cli(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: str | None = None,
        allowed_tools: str | None = None,
        model: str | None = None,
    ) -> str:
        from app.services.claude_token import ensure_fresh_token
        await ensure_fresh_token()

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        # If allowed_tools given — remove them from the DISALLOWED list, keep rest banned
        disallowed = self.DISALLOWED_TOOLS
        if allowed_tools:
            allowed_set = {t.strip() for t in allowed_tools.split(",") if t.strip()}
            disallowed = ",".join(
                t for t in self.DISALLOWED_TOOLS.split(",") if t not in allowed_set
            )

        args = [
            settings.claude_cli_path,
            "--print",
            "--output-format", "text",
            "--disallowed-tools", disallowed,
        ]
        if allowed_tools:
            args.extend(["--allowed-tools", allowed_tools])

        chosen_model = model or settings.claude_model
        if chosen_model:
            args.extend(["--model", chosen_model])
        if system_prompt:
            args.extend(["--append-system-prompt", system_prompt])

        # Don't log full args if system_prompt is huge — keep diagnostic short.
        logger.info(
            "claude CLI argv (model=%s, tools_disabled=yes, system_prompt=%s)",
            chosen_model or "default",
            f"{len(system_prompt)} chars" if system_prompt else "no",
        )

        # Run from /tmp so Claude CLI doesn't pick up the project's CLAUDE.md
        # as system context — otherwise the CLI prefixes every answer with
        # "this is off-topic for the project ArkadyJarvis…".
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd="/tmp",
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
