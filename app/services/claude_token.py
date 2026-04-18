"""Claude OAuth token auto-refresh.

Stores access + refresh tokens in data/.claude_token.json.
Refresh tokens are single-use (rotate on each refresh).
Endpoint: POST https://api.anthropic.com/v1/oauth/token
Client ID: official Claude Code CLI.
"""

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

TOKEN_FILE = Path("data/.claude_token.json")
TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
REFRESH_BUFFER_MS = 600_000  # refresh 10 min before expiry

# Protect concurrent refreshes: Anthropic refresh_token is single-use, so two
# parallel refresh calls would race, one would get 401 and the file token
# would be invalidated.
_refresh_lock = asyncio.Lock()


def _load() -> dict:
    if TOKEN_FILE.exists():
        try:
            return json.loads(TOKEN_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(data: dict) -> None:
    TOKEN_FILE.write_text(json.dumps(data, indent=2))


def init_token_file() -> None:
    """One-time init: seed token file from env vars if it doesn't exist."""
    if TOKEN_FILE.exists():
        data = _load()
        if data.get("refresh_token"):
            logger.info("Claude token file exists with refresh token")
            return

    access_token = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "")
    refresh_token = os.environ.get("CLAUDE_REFRESH_TOKEN", "")

    if not refresh_token:
        if access_token:
            logger.warning(
                "CLAUDE_CODE_OAUTH_TOKEN set but no CLAUDE_REFRESH_TOKEN — "
                "token will not auto-refresh"
            )
        return

    data = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": 0,  # force refresh on first use
    }
    _save(data)
    logger.info("Claude token file initialized from env vars")


async def ensure_fresh_token() -> None:
    """Refresh Claude OAuth token if expired. Updates os.environ.

    Safe for concurrent callers — a single refresh happens at a time;
    other callers wait on the lock and then see the fresh token in the
    file without triggering another refresh.
    """
    async with _refresh_lock:
        data = _load()
        now_ms = time.time() * 1000

        # If token is still fresh (possibly refreshed by another task while
        # we waited on the lock), just ensure env var is set.
        if data.get("expires_at", 0) > now_ms + REFRESH_BUFFER_MS:
            if data.get("access_token"):
                os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = data["access_token"]
            return

        refresh_token = data.get("refresh_token")
        if not refresh_token:
            logger.debug("No refresh token available, using current access token")
            return

        logger.info("Refreshing Claude OAuth token...")
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    TOKEN_URL,
                    data={
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                        "client_id": settings.claude_oauth_client_id,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                resp.raise_for_status()
                result = resp.json()

            new_access = result["access_token"]
            new_refresh = result["refresh_token"]
            expires_in = result.get("expires_in", 28800)  # default 8h

            new_data = {
                "access_token": new_access,
                "refresh_token": new_refresh,
                "expires_at": now_ms + expires_in * 1000,
            }
            _save(new_data)
            os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = new_access

            logger.info(
                "Claude token refreshed, expires in %d hours",
                expires_in // 3600,
            )

        except Exception as e:
            logger.error("Failed to refresh Claude token: %s", e)
            # Keep using current token, it might still work
            if data.get("access_token"):
                os.environ["CLAUDE_CODE_OAUTH_TOKEN"] = data["access_token"]
