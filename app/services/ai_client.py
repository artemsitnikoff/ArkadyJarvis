"""AIClient — текстовые вызовы Claude. Два бэкенда, переключатель `AI_BACKEND`:

- `openrouter` (default с v4.43.0) — Anthropic-модели через OpenRouter, оплата за токены.
  Подписка Claude Code для автоматизации отключена Anthropic 2026-11-04
  («Your organization has disabled Claude subscription access for Claude Code»).
- `claude_cli` (legacy) — `claude --print` subprocess по подписке. Код оставлен для
  отката, Dockerfile по-прежнему ставит CLI.

Интерфейс `complete(prompt, timeout, system_prompt, allowed_tools, model)` одинаковый
для обоих, поэтому вызывающий код не менялся. Каждый OpenRouter-вызов логирует
usage + cost (OpenRouter отдаёт стоимость в ответе при `usage.include=true`),
накопительные счётчики — в `AIClient.stats` и в GET /api/health.
"""

import asyncio
import logging
import os

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Транзиентные HTTP-статусы — один повтор через 3с.
_RETRYABLE_STATUS = {408, 409, 425, 429, 500, 502, 503, 504}


def resolve_openrouter_model(model: str | None) -> str:
    """CLI-имя модели → OpenRouter ID.

    - пусто → `OPENROUTER_TEXT_MODEL` (по умолчанию Sonnet 5)
    - уже OpenRouter-ID (`anthropic/...`, содержит «/») → как есть
    - любое имя с «haiku» (напр. `claude-haiku-4-5-20251001` у классификаторов)
      → `OPENROUTER_HAIKU_MODEL`
    - прочее CLI-имя (opus/sonnet) → дефолтная модель + warning
    """
    m = (model or "").strip()
    if not m:
        return settings.openrouter_text_model
    if "/" in m:
        return m
    if "haiku" in m.lower():
        return settings.openrouter_haiku_model
    logger.warning(
        "AIClient: модель %r не маппится на OpenRouter — беру %s",
        m, settings.openrouter_text_model,
    )
    return settings.openrouter_text_model


class AIClient:
    """Текстовый AI-клиент. См. докстринг модуля."""

    def __init__(self):
        self.backend = (settings.ai_backend or "openrouter").strip().lower()
        self.stats = {
            "calls": 0, "errors": 0,
            "prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0,
        }
        self._http: httpx.AsyncClient | None = None
        if self.backend == "openrouter":
            if not settings.openrouter_api_key.get_secret_value():
                logger.error("AI_BACKEND=openrouter, но OPENROUTER_API_KEY пуст — все AI-вызовы упадут")
            self._http = httpx.AsyncClient(
                timeout=httpx.Timeout(connect=10.0, read=settings.openrouter_timeout, write=60.0, pool=10.0),
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://github.com/digital-clouds/ArkadyJarvis",
                    "X-Title": "ArkadyJarvis",
                },
            )
        logger.info(
            "AIClient backend=%s model=%s haiku=%s web_search=%s",
            self.backend, settings.openrouter_text_model, settings.openrouter_haiku_model,
            settings.openrouter_web_search,
        )

    async def complete(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: str | None = None,
        allowed_tools: str | None = None,
        model: str | None = None,
    ) -> str:
        """Один текстовый запрос → текст ответа.

        `allowed_tools`: "WebSearch,WebFetch" — на OpenRouter включает web-плагин
        (поиск провайдера, ~$0.01/поиск), на CLI — whitelist тулзов.
        `model`: CLI-имя или OpenRouter-ID; пусто = дефолт бэкенда.
        """
        if self.backend == "claude_cli":
            return await self._call_cli(
                prompt, timeout=timeout, system_prompt=system_prompt,
                allowed_tools=allowed_tools, model=model,
            )
        return await self._call_openrouter(
            prompt, timeout=timeout, system_prompt=system_prompt,
            allowed_tools=allowed_tools, model=model,
        )

    # ── OpenRouter ───────────────────────────────────────────────

    async def _call_openrouter(
        self,
        prompt: str,
        timeout: int = 120,
        system_prompt: str | None = None,
        allowed_tools: str | None = None,
        model: str | None = None,
    ) -> str:
        assert self._http is not None
        model_id = resolve_openrouter_model(model)
        messages: list[dict] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        payload: dict = {
            "model": model_id,
            "messages": messages,
            "max_tokens": settings.openrouter_max_tokens,
            "usage": {"include": True},
        }
        web = bool(
            settings.openrouter_web_search and allowed_tools
            and ("websearch" in allowed_tools.lower() or "webfetch" in allowed_tools.lower())
        )
        if web:
            payload["plugins"] = [{"id": "web"}]

        logger.info(
            "AI openrouter → %s (prompt=%d chars, system=%s, web=%s, timeout=%ds)",
            model_id, len(prompt),
            f"{len(system_prompt)} chars" if system_prompt else "no", web, timeout,
        )
        req_timeout = httpx.Timeout(connect=10.0, read=float(timeout), write=60.0, pool=10.0)

        last_exc: Exception | None = None
        for attempt in (1, 2):
            try:
                resp = await self._http.post(OPENROUTER_URL, json=payload, timeout=req_timeout)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_exc = e
                if isinstance(e, httpx.TimeoutException):
                    logger.error("AI openrouter timeout %ds on %s (attempt %d)", timeout, model_id, attempt)
                else:
                    logger.error("AI openrouter transport error on %s (attempt %d): %s", model_id, attempt, e)
                if attempt == 1:
                    await asyncio.sleep(3)
                    continue
                self.stats["errors"] += 1
                if isinstance(e, httpx.TimeoutException):
                    raise TimeoutError(f"AI не ответил за {timeout}с") from e
                raise RuntimeError(f"OpenRouter недоступен: {e}") from e

            if resp.status_code in _RETRYABLE_STATUS and attempt == 1:
                logger.warning(
                    "AI openrouter HTTP %d on %s — повтор через 3с: %s",
                    resp.status_code, model_id, resp.text[:200],
                )
                await asyncio.sleep(3)
                continue
            if resp.status_code >= 400:
                self.stats["errors"] += 1
                body = resp.text[:300]
                logger.error("AI openrouter HTTP %d on %s: %s", resp.status_code, model_id, body)
                raise RuntimeError(f"OpenRouter {resp.status_code}: {body}")

            data = resp.json()
            return self._extract_text(data, model_id)

        # unreachable, but keeps type-checkers calm
        raise RuntimeError(f"OpenRouter: {last_exc}")

    def _extract_text(self, data: dict, model_id: str) -> str:
        if data.get("error") and not data.get("choices"):
            self.stats["errors"] += 1
            err = data["error"]
            raise RuntimeError(f"OpenRouter: {err.get('message') or err}")
        choices = data.get("choices") or []
        if not choices:
            self.stats["errors"] += 1
            raise RuntimeError("OpenRouter не вернул choices")
        choice = choices[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            content = "".join(p.get("text", "") for p in content if p.get("type") == "text")
        text = (content or "").strip()
        finish = choice.get("finish_reason")

        usage = data.get("usage") or {}
        p_tok = int(usage.get("prompt_tokens") or 0)
        c_tok = int(usage.get("completion_tokens") or 0)
        cost = float(usage.get("cost") or 0.0)
        self.stats["calls"] += 1
        self.stats["prompt_tokens"] += p_tok
        self.stats["completion_tokens"] += c_tok
        self.stats["cost_usd"] += cost
        logger.info(
            "AI openrouter ← %s: in=%d out=%d cost=$%.4f finish=%s (session: %d calls, $%.3f)",
            model_id, p_tok, c_tok, cost, finish, self.stats["calls"], self.stats["cost_usd"],
        )
        if finish == "length":
            logger.warning(
                "AI openrouter: ответ обрезан по max_tokens=%d (%s)",
                settings.openrouter_max_tokens, model_id,
            )
        if not text:
            self.stats["errors"] += 1
            choice_err = choice.get("error") or {}
            detail = choice_err.get("message") or msg.get("refusal") or f"finish_reason={finish}"
            raise RuntimeError(f"AI вернул пустой ответ ({detail})")
        return text

    # ── Claude CLI (legacy, AI_BACKEND=claude_cli) ───────────────

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
        if self._http is not None:
            await self._http.aclose()
            self._http = None
