#!/usr/bin/env python3
"""Принудительный рефреш Claude OAuth-токена.

Дёргает ту же ensure_fresh_token, что вызывается перед каждым AI-запросом.
После рефреша синхронизирует ~/.claude/credentials.json — чтобы прямой
вызов `claude` в контейнере тоже работал, а не только Python-wrapper.

    docker compose exec bot python scripts/refresh_claude_token.py
"""
import asyncio
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from app.services.claude_token import (  # noqa: E402
    CLI_CREDENTIALS_FILE,
    TOKEN_FILE,
    ensure_fresh_token,
    init_token_file,
)


async def main() -> None:
    init_token_file()
    await ensure_fresh_token()
    if not Path(TOKEN_FILE).exists():
        print("ERROR: токен-файл не появился — проверь .env "
              "(CLAUDE_CODE_OAUTH_TOKEN + CLAUDE_REFRESH_TOKEN)")
        sys.exit(1)
    data = json.loads(Path(TOKEN_FILE).read_text())
    exp = datetime.fromtimestamp(data.get("expires_at", 0) / 1000)
    print(f"✓ Токен обновлён, валиден до {exp:%Y-%m-%d %H:%M}")
    print(f"  {TOKEN_FILE} — для Python-обёртки")
    print(f"  {CLI_CREDENTIALS_FILE} — для прямого `claude` (синхронизирован)")
    print()
    print("Проверка прямого вызова `claude --print '1+1='`...")

    proc = await asyncio.create_subprocess_exec(
        "claude", "--print", "--output-format", "text", "1+1=",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=30,
        )
    except asyncio.TimeoutError:
        proc.kill()
        print("✗ CLI timeout (30s) — токен сохранён, но CLI висит. "
              "Скорее всего subscription weekly quota.")
        sys.exit(1)
    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode == 0:
        print(f"✓ CLI: {out!r}")
    else:
        print(f"✗ CLI rc={proc.returncode}")
        print(f"  stderr: {err[:300]}")
        print(f"  stdout: {out[:300]}")


asyncio.run(main())
