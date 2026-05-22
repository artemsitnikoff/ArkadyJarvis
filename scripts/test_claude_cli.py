#!/usr/bin/env python3
"""Diagnostic: проверяет работу Claude CLI через Python-обёртку.

1. Без тулов — должен ответить за 5-10с (проверка auth).
2. С WebSearch+WebFetch — должен ответить за 10-30с (проверка тулов).

Если первый ОК, второй виснет — упёрлись в weekly quota subscription
именно на tool-use, основной b24-скрипт работать тоже не будет.

    docker compose exec bot python scripts/test_claude_cli.py
"""
import asyncio
import logging
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

from app.services.ai_client import AIClient  # noqa: E402


async def main() -> None:
    ai = AIClient()

    print("\n=== 1. БЕЗ ТУЛОВ (auth-check) ===")
    t0 = time.monotonic()
    try:
        r = await ai.complete("Скажи только: жив", timeout=30)
        dt = time.monotonic() - t0
        print(f"✓ {dt:.1f}s — {r[:200]!r}")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"✗ {dt:.1f}s — {e}")

    print("\n=== 2. С WEBSEARCH+WEBFETCH (tool-check) ===")
    t0 = time.monotonic()
    try:
        r = await ai.complete(
            "Сделай ровно один WebSearch по запросу 'Anthropic' "
            "и верни первый заголовок одной строкой. Только заголовок.",
            timeout=180,
            allowed_tools="WebSearch,WebFetch",
        )
        dt = time.monotonic() - t0
        print(f"✓ {dt:.1f}s — {r[:300]!r}")
    except Exception as e:
        dt = time.monotonic() - t0
        print(f"✗ {dt:.1f}s — {e}")


asyncio.run(main())
