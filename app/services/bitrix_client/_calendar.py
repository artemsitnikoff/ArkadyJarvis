import logging
from datetime import datetime, timedelta

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class _BitrixCalendarMixin:
    """Calendar-related Bitrix24 methods."""

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

    async def get_user_events(self, user_id: int) -> list[dict]:
        """Fetch user's calendar events for today."""
        now = datetime.now()
        date_from = now.strftime("%Y-%m-%dT%H:%M:%S")
        date_to = now.replace(hour=23, minute=59, second=59).strftime("%Y-%m-%dT%H:%M:%S")

        result = await self._request("calendar.event.get", {
            "type": "user",
            "ownerId": user_id,
            "from": date_from,
            "to": date_to,
        })
        events = result.get("result", [])

        # Filter: only future, not deleted
        filtered = []
        for ev in events:
            if ev.get("DELETED") == "Y":
                continue
            filtered.append({
                "id": ev["ID"],
                "name": ev.get("NAME", ""),
                "date_from": ev.get("DATE_FROM", ""),
                "date_to": ev.get("DATE_TO", ""),
                "owner_id": ev.get("OWNER_ID"),
            })

        # Sort by date_from
        filtered.sort(key=lambda e: e["date_from"])
        return filtered
