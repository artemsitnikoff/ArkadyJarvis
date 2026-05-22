#!/usr/bin/env python3
"""Принудительный рефреш Claude OAuth-токена через app.services.claude_token.

Использует refresh_token из data/.claude_token.json. Печатает новые
access/refresh пары и удобную строку для .env.

    docker compose exec bot python scripts/refresh_claude_token.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from app.services.claude_token import (  # noqa: E402
    TOKEN_FILE,
    ensure_fresh_token,
    init_token_file,
)


async def main() -> None:
    init_token_file()
    await ensure_fresh_token()

    if not Path(TOKEN_FILE).exists():
        print("ERROR: токен-файл не появился — проверь .env (нужны "
              "CLAUDE_CODE_OAUTH_TOKEN и CLAUDE_REFRESH_TOKEN)")
        sys.exit(1)

    data = json.loads(Path(TOKEN_FILE).read_text())
    print()
    print("=" * 60)
    print("Свежие токены сохранены в", TOKEN_FILE)
    print("=" * 60)
    print(f"access_token : {data.get('access_token','')[:50]}…")
    print(f"refresh_token: {data.get('refresh_token','')[:50]}…")
    print(f"expires_at   : {data.get('expires_at')}")
    print()
    print("Чтобы синхронизировать с .env (нужно если хочешь чтобы прямой "
          "вызов `claude` тоже работал, а не только Python-wrapper):")
    print()
    print(f"CLAUDE_CODE_OAUTH_TOKEN={data.get('access_token','')}")
    print(f"CLAUDE_REFRESH_TOKEN={data.get('refresh_token','')}")

    # Проверим что прямой CLI теперь авторизован с этим токеном
    print()
    print("Проверка: запускаю `claude --print '1+1='` с обновлённым env...")
    env = os.environ.copy()
    env["CLAUDE_CODE_OAUTH_TOKEN"] = data["access_token"]
    proc = await asyncio.create_subprocess_exec(
        "claude", "--print", "--output-format", "text", "1+1=",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=30,
        )
    except asyncio.TimeoutError:
        proc.kill()
        print("CLI timeout (30s) — токен есть, но CLI висит. Проверь "
              "subscription/квоту.")
        sys.exit(1)
    out = stdout.decode(errors="replace").strip()
    err = stderr.decode(errors="replace").strip()
    if proc.returncode == 0:
        print(f"✓ CLI ответил: {out!r}")
    else:
        print(f"✗ CLI ошибка (rc={proc.returncode}):")
        print(f"   stdout: {out[:300]}")
        print(f"   stderr: {err[:300]}")


asyncio.run(main())
