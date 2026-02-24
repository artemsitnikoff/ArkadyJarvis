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

        fields = ("PERSONAL_MOBILE", "PERSONAL_PHONE", "WORK_PHONE")
        variants = [phone, f"+{digits}", digits]

        commands = {}
        for field in fields:
            for variant in variants:
                key = f"{field}__{variant}"
                commands[key] = ("user.get", {"filter": {field: variant}})

        results = await self._batch_request(commands)

        for field in fields:
            for variant in variants:
                key = f"{field}__{variant}"
                users = results.get(key, [])
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
        variants = [clean, f"@{clean}"]

        commands = {
            v: ("user.get", {"filter": {"UF_USR_1678964886664": v}})
            for v in variants
        }

        results = await self._batch_request(commands)

        for v in variants:
            users = results.get(v, [])
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

        chunk_size = 100
        all_chunks = []
        for start in range(1, max_id + 1, chunk_size):
            ids = list(range(start, min(start + chunk_size, max_id + 1)))
            all_chunks.append(ids)

        # Group chunks into batches of 50 (Bitrix batch limit)
        batch_limit = 50
        for i in range(0, len(all_chunks), batch_limit):
            batch_chunks = all_chunks[i:i + batch_limit]
            commands = {
                f"chunk_{ids[0]}": ("im.user.list.get", {"ID": ids})
                for ids in batch_chunks
            }

            try:
                results = await self._batch_request(commands)
            except Exception:
                continue

            for key, user_map in results.items():
                if not isinstance(user_map, dict):
                    continue
                for uid_str, u in user_map.items():
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
