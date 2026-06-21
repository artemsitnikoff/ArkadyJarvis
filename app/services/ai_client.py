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

        # Whitelist tools via --tools, never a deny-list. "" disables ALL
        # built-in tools (prompt-only — no shell/file/web, so no RCE when a
        # user prompt looks like an instruction "сделай cd ..., ls ...").
        # A deny-list (--disallowed-tools) breaks the moment the CLI renames or
        # removes a tool: e.g. "MultiEdit" → CLI exits code 1 with "matches no
        # known tool", killing EVERY AI call on a newer CLI build — and it also
        # silently misses any newly-added tool. Whitelisting is robust across
        # CLI versions and strictly safer. allowed_tools (read-only, e.g.
        # "WebSearch,WebFetch" for Stirlitz) passes through verbatim.
        args = [
            settings.claude_cli_path,
            "--print",
            "--output-format", "text",
            "--tools", allowed_tools or "",
        ]

        chosen_model = model or settings.claude_model
        if chosen_model:
            args.extend(["--model", chosen_model])
        if system_prompt:
            args.extend(["--append-system-prompt", system_prompt])

        # Don't log full args if system_prompt is huge — keep diagnostic short.
        logger.info(
            "claude CLI argv (model=%s, tools=%s, system_prompt=%s)",
            chosen_model or "default",
            allowed_tools or "none",
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
            try:
                # читаем что успело упасть в потоки, чтобы понять причину тишины
                stdout_partial, stderr_partial = await asyncio.wait_for(
                    proc.communicate(), timeout=2,
                )
                out_tail = (stdout_partial or b"").decode(errors="replace").strip()[-400:]
                err_tail = (stderr_partial or b"").decode(errors="replace").strip()[-400:]
                logger.error(
                    "claude CLI timeout %ds — stderr_tail=%r stdout_tail=%r",
                    timeout, err_tail, out_tail,
                )
            except Exception:
                pass
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
