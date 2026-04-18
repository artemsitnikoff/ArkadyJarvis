#!/usr/bin/env python3
"""Send the Monday Soviet-1930s motivational poster to a test chat right now.

Usage:
    python scripts/test_monday_poster.py              # default test chat -790607108
    python scripts/test_monday_poster.py <chat_id>    # custom chat id
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot.create import create_bot  # noqa: E402
from app.scheduler.jobs import send_monday_poster  # noqa: E402
from app.services.ai_client import AIClient  # noqa: E402
from app.services.claude_token import init_token_file  # noqa: E402
from app.services.openrouter_client import OpenRouterClient  # noqa: E402

DEFAULT_TEST_CHAT_ID = -790607108


async def main():
    chat_id = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TEST_CHAT_ID

    init_token_file()
    bot = create_bot()
    ai_client = AIClient()
    openrouter = OpenRouterClient()

    try:
        print(f"Sending Monday poster to chat {chat_id}...")
        await send_monday_poster(bot, ai_client, openrouter, chat_id)
        print("Done.")
    finally:
        await bot.session.close()
        await ai_client.close()
        await openrouter.close()


if __name__ == "__main__":
    asyncio.run(main())
