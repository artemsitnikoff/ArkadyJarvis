#!/usr/bin/env python3
"""Send the Wednesday frog meme to a test chat right now (bypasses schedule).

Usage:
    python scripts/test_wednesday_frog.py              # default test chat -790607108
    python scripts/test_wednesday_frog.py <chat_id>    # custom chat id
"""

import asyncio
import sys

from app.bot.create import create_bot
from app.scheduler.jobs import send_wednesday_frog
from app.services.ai_client import AIClient
from app.services.claude_token import init_token_file
from app.services.openrouter_client import OpenRouterClient

DEFAULT_TEST_CHAT_ID = -790607108


async def main():
    chat_id = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TEST_CHAT_ID

    init_token_file()
    bot = create_bot()
    ai_client = AIClient()
    openrouter = OpenRouterClient()

    try:
        print(f"Sending Wednesday frog to chat {chat_id}...")
        await send_wednesday_frog(bot, ai_client, openrouter, chat_id)
        print("Done.")
    finally:
        await bot.session.close()
        await ai_client.close()
        await openrouter.close()


if __name__ == "__main__":
    asyncio.run(main())
