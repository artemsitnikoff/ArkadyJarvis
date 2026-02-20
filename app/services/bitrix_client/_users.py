import logging
import re

logger = logging.getLogger("arkadyjarvis")


class _BitrixUsersMixin:
    """User-related Bitrix24 methods."""

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
