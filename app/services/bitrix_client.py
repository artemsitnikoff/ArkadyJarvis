import json
import logging
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

TOKENS_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "bitrix_tokens.json"
OAUTH_URL = "https://oauth.bitrix24.tech/oauth/token"


class BitrixClient:
    """Singleton Bitrix24 client with shared token (like SmartSummary)."""

    _instance: "BitrixClient | None" = None

    def __init__(self):
        self._http = httpx.AsyncClient()
        self._email_guests_cache: dict[str, tuple[int, str]] = {}
        self._email_guests_loaded = False

    @classmethod
    def get(cls) -> "BitrixClient":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    async def close(self):
        await self._http.aclose()

    # ── Token management (file-based, shared) ─────────────────

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
        TOKENS_FILE.write_text(json.dumps(tokens, indent=2))
        logger.info("Bitrix tokens saved (endpoint: %s)", tokens["client_endpoint"])

    async def _refresh_access_token(self, refresh_token: str) -> dict:
        resp = await self._http.get(
            OAUTH_URL,
            params={
                "grant_type": "refresh_token",
                "client_id": settings.bitrix_client_id,
                "client_secret": settings.bitrix_client_secret,
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
        tokens = self._load_tokens()

        if tokens is None:
            if not settings.bitrix_refresh_token:
                raise RuntimeError("BITRIX_REFRESH_TOKEN не задан в .env")
            logger.info("Bitrix: first run, refreshing from .env token...")
            return await self._refresh_access_token(settings.bitrix_refresh_token)

        if time.time() < tokens["expires_at"] - 60:
            return tokens

        logger.info("Bitrix access_token expired, refreshing...")
        return await self._refresh_access_token(tokens["refresh_token"])

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

    # ── Public API ────────────────────────────────────────────────

    async def find_user_by_phone(self, phone: str) -> tuple[int | None, str | None]:
        """Search Bitrix user by phone number. Tries PERSONAL_MOBILE, PERSONAL_PHONE, WORK_PHONE."""
        digits = re.sub(r"\D", "", phone)
        if digits.startswith("8") and len(digits) == 11:
            digits = "7" + digits[1:]

        for field in ("PERSONAL_MOBILE", "PERSONAL_PHONE", "WORK_PHONE"):
            for variant in [phone, f"+{digits}", digits]:
                result = await self._request("user.get", {
                    "filter": {field: variant},
                })
                users = result.get("result", [])
                if users:
                    user = users[0]
                    full_name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                    logger.info(
                        "Bitrix user found by phone %s: id=%s name=%s (field=%s)",
                        phone, user["ID"], full_name, field,
                    )
                    return int(user["ID"]), full_name
        return None, None

    async def find_user_by_nickname(self, nickname: str) -> tuple[int | None, str | None]:
        clean = nickname.lstrip("@")
        for variant in [clean, f"@{clean}"]:
            result = await self._request("user.get", {
                "filter": {"UF_USR_1678964886664": variant},
            })
            users = result.get("result", [])
            if users:
                user = users[0]
                full_name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
                return int(user["ID"]), full_name
        return None, None

    async def find_user_by_email(self, email: str) -> tuple[int | None, str | None]:
        result = await self._request("user.get", {
            "filter": {"EMAIL": email},
        })
        users = result.get("result", [])
        if users:
            user = users[0]
            full_name = f"{user.get('NAME', '')} {user.get('LAST_NAME', '')}".strip()
            return int(user["ID"]), full_name
        return None, None

    async def _load_email_guests(self):
        if self._email_guests_loaded:
            return

        result = await self._request("user.get", {"start": 0})
        total_regular = result.get("total", 0)
        max_id = max(total_regular * 3, 2000)

        batch_size = 100
        for start in range(1, max_id + 1, batch_size):
            ids = list(range(start, min(start + batch_size, max_id + 1)))
            try:
                result = await self._request("im.user.list.get", {"ID": ids})
            except Exception:
                continue
            for uid_str, u in result.get("result", {}).items():
                if u and u.get("external_auth_id") == "email" and u.get("email"):
                    email = u["email"].lower()
                    self._email_guests_cache[email] = (u["id"], u.get("name", ""))

        self._email_guests_loaded = True
        logger.info("Loaded %d email guests from Bitrix", len(self._email_guests_cache))

    async def resolve_email_user(self, email: str) -> tuple[int | None, str | None]:
        uid, name = await self.find_user_by_email(email)
        if uid:
            return uid, name

        await self._load_email_guests()
        cached = self._email_guests_cache.get(email.lower())
        if cached:
            return cached

        return None, None

    async def get_users_accessibility(
        self, user_ids: list[int], date_from: str, date_to: str
    ) -> dict:
        result = await self._request("calendar.accessibility.get", {
            "users": user_ids,
            "from": date_from,
            "to": date_to,
        })
        return result.get("result", {})

    async def create_meeting(
        self,
        title: str,
        date: datetime,
        owner_user_id: int,
        description: str = "",
        duration_minutes: int = 60,
        attendee_ids: list[int] | None = None,
    ) -> dict:
        date_from = date.strftime("%d.%m.%Y %H:%M:%S")
        date_to = (date + timedelta(minutes=duration_minutes)).strftime("%d.%m.%Y %H:%M:%S")

        event_params = {
            "type": "user",
            "ownerId": owner_user_id,
            "name": title,
            "description": description,
            "from": date_from,
            "to": date_to,
            "timezone_from": settings.timezone,
            "timezone_to": settings.timezone,
        }

        if attendee_ids:
            all_ids = [owner_user_id] + [aid for aid in attendee_ids if aid != owner_user_id]
            event_params.update({
                "is_meeting": "Y",
                "host": owner_user_id,
                "attendees": all_ids,
                "meeting": {
                    "notify": True,
                    "open": False,
                    "reinvite": False,
                },
            })

        result = await self._request("calendar.event.add", event_params)
        event_id = result.get("result")
        logger.info(
            "Bitrix calendar event created: id=%s title=%s date=%s attendees=%s",
            event_id, title, date_from, attendee_ids,
        )
        return {"status": "ok", "id": event_id, "user_id": owner_user_id}

    async def create_lead(self, fields: dict) -> dict:
        result = await self._request("crm.lead.add", {"fields": fields})
        lead_id = result.get("result")
        logger.info("Bitrix lead created: id=%s title=%s", lead_id, fields.get("TITLE"))
        return {"status": "ok", "id": lead_id}
