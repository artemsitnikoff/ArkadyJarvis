import asyncio
import json
import logging
import os
import time
from pathlib import Path
from urllib.parse import urlencode

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

TOKENS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "bitrix_tokens.json"
OAUTH_URL = "https://oauth.bitrix24.tech/oauth/token"


class _BitrixBase:
    """Token management and HTTP request layer for Bitrix24."""

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30.0)
        self._token_lock = asyncio.Lock()

    async def close(self):
        await self._http.aclose()

    def _load_tokens(self) -> dict | None:
        if not TOKENS_FILE.exists():
            return None
        try:
            return json.loads(TOKENS_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            logger.error("Failed to load Bitrix tokens: %s", e)
            return None

    def _save_tokens(self, data: dict):
        tokens = {
            "access_token": data["access_token"],
            "refresh_token": data["refresh_token"],
            "client_endpoint": data["client_endpoint"],
            "expires_at": int(time.time()) + int(data.get("expires_in", 3600)),
        }
        TOKENS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Atomic rename — a kill mid-write would leave a half-JSON file
        # and brick the entire Bitrix client until someone restores it
        # manually.
        tmp = TOKENS_FILE.with_suffix(TOKENS_FILE.suffix + ".tmp")
        tmp.write_text(json.dumps(tokens, indent=2))
        os.replace(tmp, TOKENS_FILE)
        logger.info("Bitrix tokens saved (endpoint: %s)", tokens["client_endpoint"])

    async def _refresh_access_token(self, refresh_token: str) -> dict:
        resp = await self._http.get(
            OAUTH_URL,
            params={
                "grant_type": "refresh_token",
                "client_id": settings.bitrix_client_id,
                "client_secret": settings.bitrix_client_secret.get_secret_value(),
                "refresh_token": refresh_token,
            },
        )
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            raise RuntimeError(
                f"Bitrix refresh error: {data['error']} — {data.get('error_description', '')}"
            )

        self._save_tokens(data)
        return self._load_tokens()

    async def _get_tokens(self) -> dict:
        async with self._token_lock:
            tokens = self._load_tokens()
            if tokens and time.time() < tokens["expires_at"] - 60:
                return tokens
            refresh = (tokens or {}).get("refresh_token") or settings.bitrix_refresh_token
            if not refresh:
                raise RuntimeError("BITRIX_REFRESH_TOKEN не задан в .env")
            return await self._refresh_access_token(refresh)

    @staticmethod
    def _flatten_params(params: dict, prefix: str = "") -> dict[str, str]:
        """Flatten nested dicts/lists into Bitrix query-string keys.

        {"filter": {"FIELD": "val"}} → {"filter[FIELD]": "val"}
        {"ID": [1, 2]} → {"ID[0]": "1", "ID[1]": "2"}
        """
        flat: dict[str, str] = {}
        for key, value in params.items():
            full_key = f"{prefix}[{key}]" if prefix else key
            if isinstance(value, dict):
                flat.update(_BitrixBase._flatten_params(value, full_key))
            elif isinstance(value, (list, tuple)):
                for i, item in enumerate(value):
                    flat[f"{full_key}[{i}]"] = str(item)
            else:
                flat[full_key] = str(value)
        return flat

    async def _batch_request(self, commands: dict[str, tuple[str, dict]]) -> dict:
        """Execute multiple Bitrix API calls in one HTTP request (up to 50).

        Args:
            commands: {'label': ('method', {params}), ...}
                params are normal Python dicts, same format as _request().

        Returns:
            {'label': <result>, ...} — results keyed by the same labels.
        """
        tokens = await self._get_tokens()
        url = f"{tokens['client_endpoint']}batch"

        cmd = {}
        for label, (method, params) in commands.items():
            if params:
                cmd[label] = f"{method}?{urlencode(self._flatten_params(params))}"
            else:
                cmd[label] = method

        body = {"auth": tokens["access_token"], "cmd": cmd}
        resp = await self._http.post(url, json=body)
        data = resp.json()

        if not resp.is_success or "error" in data:
            error = data.get("error", resp.status_code)
            desc = data.get("error_description", resp.reason_phrase)
            raise RuntimeError(f"Bitrix batch error: {error} — {desc}")

        return data.get("result", {}).get("result", {})

    async def _request(self, method: str, params: dict | None = None) -> dict:
        tokens = await self._get_tokens()
        url = f"{tokens['client_endpoint']}{method}"

        body = dict(params or {})
        body["auth"] = tokens["access_token"]

        resp = await self._http.post(url, json=body)
        data = resp.json()

        if not resp.is_success or "error" in data:
            error = data.get("error", resp.status_code)
            desc = data.get("error_description", resp.reason_phrase)
            raise RuntimeError(f"Bitrix API error ({method}): {error} — {desc}")

        return data
