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

    async def search_users(self, query: str, limit: int = 5) -> list[dict]:
        """Search active Bitrix users by name/surname partial match."""
        commands = {
            "by_name": ("user.get", {"filter": {"ACTIVE": True, "%NAME": query}}),
            "by_last": ("user.get", {"filter": {"ACTIVE": True, "%LAST_NAME": query}}),
        }
        results = await self._batch_request(commands)

        seen: set[int] = set()
        users: list[dict] = []
        for key in ("by_name", "by_last"):
            for u in results.get(key, []):
                uid = int(u["ID"])
                if uid not in seen:
                    seen.add(uid)
                    name = f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip()
                    users.append({"id": uid, "name": name})
                    if len(users) >= limit:
                        return users
        return users

    async def get_employee_card(self, user_id: int) -> dict | None:
        """Fetch detailed employee card: name, position, department, phone, email, supervisor."""
        result = await self._request("user.get", {"ID": user_id})
        users = result.get("result", [])
        if not users:
            return None

        u = users[0]
        card = {
            "id": int(u["ID"]),
            "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip(),
            "position": u.get("WORK_POSITION", ""),
            "email": u.get("EMAIL", ""),
            "phone": u.get("PERSONAL_MOBILE") or u.get("WORK_PHONE") or u.get("PERSONAL_PHONE") or "",
            "telegram": u.get("UF_USR_1678964886664", ""),
            "department_ids": u.get("UF_DEPARTMENT", []),
            "photo": u.get("PERSONAL_PHOTO", ""),
        }

        # Fetch department names
        if card["department_ids"]:
            dept_commands = {
                f"dept_{did}": ("department.get", {"ID": did})
                for did in card["department_ids"]
            }
            dept_results = await self._batch_request(dept_commands)
            dept_names = []
            for did in card["department_ids"]:
                depts = dept_results.get(f"dept_{did}", [])
                if depts:
                    dept_names.append(depts[0].get("NAME", ""))
            card["departments"] = dept_names

            # Find supervisor: get department head
            first_dept = dept_results.get(f"dept_{card['department_ids'][0]}", [])
            if first_dept:
                head_id = first_dept[0].get("UF_HEAD")
                if head_id and int(head_id) != user_id:
                    head_result = await self._request("user.get", {"ID": head_id})
                    head_users = head_result.get("result", [])
                    if head_users:
                        h = head_users[0]
                        card["supervisor"] = {
                            "id": int(h["ID"]),
                            "name": f"{h.get('NAME', '')} {h.get('LAST_NAME', '')}".strip(),
                            "position": h.get("WORK_POSITION", ""),
                        }

        return card

    @staticmethod
    def _brief_user(u: dict) -> dict:
        return {
            "id": int(u["ID"]),
            "name": f"{u.get('NAME', '')} {u.get('LAST_NAME', '')}".strip(),
            "position": u.get("WORK_POSITION", ""),
        }

    async def get_my_team(self, user_id: int) -> dict | None:
        """Fetch team hierarchy: supervisor, colleagues/subordinates, with work status."""
        # Step 1: Get user + department info + department members in one batch
        result = await self._request("user.get", {"ID": user_id})
        users = result.get("result", [])
        if not users:
            return None

        me = users[0]
        dept_ids = me.get("UF_DEPARTMENT", [])
        if not dept_ids:
            return {"department": "", "is_head": False, "supervisor": None,
                    "colleagues": [], "subordinates": []}

        primary_dept_id = dept_ids[0]

        batch1 = {
            "dept": ("department.get", {"ID": primary_dept_id}),
            "members": ("user.get", {
                "filter": {"UF_DEPARTMENT": primary_dept_id, "ACTIVE": True},
            }),
        }
        r1 = await self._batch_request(batch1)

        dept_list = r1.get("dept", [])
        if not dept_list:
            return None
        dept = dept_list[0]
        dept_name = dept.get("NAME", "")
        head_id = int(dept["UF_HEAD"]) if dept.get("UF_HEAD") else None
        parent_dept_id = int(dept["PARENT"]) if dept.get("PARENT") else None
        members = r1.get("members", [])
        is_head = (head_id == user_id)

        supervisor = None
        colleagues = []
        subordinates = []

        if is_head:
            # Subordinates = other department members
            for m in members:
                mid = int(m["ID"])
                if mid != user_id:
                    subordinates.append(self._brief_user(m))

            # Supervisor = parent department head; peers = sibling dept heads
            if parent_dept_id:
                batch2 = {
                    "parent_dept": ("department.get", {"ID": parent_dept_id}),
                    "all_depts": ("department.get", {}),
                }
                r2 = await self._batch_request(batch2)

                parent_info = r2.get("parent_dept", [])
                if parent_info:
                    parent_head_id = int(parent_info[0]["UF_HEAD"]) if parent_info[0].get("UF_HEAD") else None
                    if parent_head_id and parent_head_id != user_id:
                        sup_result = await self._request("user.get", {"ID": parent_head_id})
                        sup_users = sup_result.get("result", [])
                        if sup_users:
                            supervisor = self._brief_user(sup_users[0])

                # Find sibling department heads (peers)
                all_depts = r2.get("all_depts", [])
                sibling_head_ids = []
                for d in all_depts:
                    if (d.get("PARENT") and int(d["PARENT"]) == parent_dept_id
                            and int(d["ID"]) != primary_dept_id and d.get("UF_HEAD")):
                        sid = int(d["UF_HEAD"])
                        if sid != user_id:
                            sibling_head_ids.append(sid)

                if sibling_head_ids:
                    peer_cmds = {
                        f"peer_{pid}": ("user.get", {"ID": pid})
                        for pid in sibling_head_ids[:20]
                    }
                    peer_results = await self._batch_request(peer_cmds)
                    for pid in sibling_head_ids[:20]:
                        pu = peer_results.get(f"peer_{pid}", [])
                        if pu:
                            colleagues.append(self._brief_user(pu[0]))
        else:
            # Regular employee — supervisor is dept head, colleagues are peers
            for m in members:
                mid = int(m["ID"])
                if mid == user_id:
                    continue
                if mid == head_id:
                    supervisor = self._brief_user(m)
                else:
                    colleagues.append(self._brief_user(m))

            # Head might not be in the department members list
            if head_id and not supervisor:
                sup_result = await self._request("user.get", {"ID": head_id})
                sup_users = sup_result.get("result", [])
                if sup_users:
                    supervisor = self._brief_user(sup_users[0])

        # Step 3: Get timeman.status for all team members
        all_team = []
        if supervisor:
            all_team.append(supervisor)
        all_team.extend(colleagues)
        all_team.extend(subordinates)

        if all_team:
            tm_cmds = {
                f"tm_{p['id']}": ("timeman.status", {"USER_ID": p["id"]})
                for p in all_team[:45]
            }
            try:
                tm_results = await self._batch_request(tm_cmds)
                for p in all_team[:45]:
                    tm = tm_results.get(f"tm_{p['id']}")
                    if isinstance(tm, dict):
                        p["work_status"] = tm.get("STATUS", "")
                        p["work_start"] = tm.get("TIME_START", "")
            except Exception as e:
                logger.warning("timeman.status failed (may not be enabled): %s", e)

        return {
            "department": dept_name,
            "is_head": is_head,
            "supervisor": supervisor,
            "colleagues": colleagues,
            "subordinates": subordinates,
        }

    async def get_user_email(self, user_id: int) -> str | None:
        """Get user email by Bitrix user ID."""
        result = await self._request("user.get", {"ID": user_id})
        users = result.get("result", [])
        if users:
            return users[0].get("EMAIL")
        return None

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
