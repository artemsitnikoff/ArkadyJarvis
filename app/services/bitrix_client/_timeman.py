import logging

logger = logging.getLogger("arkadyjarvis")


class _BitrixTimemanMixin:
    """Timeman (work day) methods for Bitrix24."""

    async def get_work_status(self, user_id: int) -> dict | None:
        """Get user's current work day status.

        Returns dict with status (OPENED/CLOSED/PAUSED/EXPIRED) and time_start.
        """
        try:
            result = await self._request("timeman.status", {"USER_ID": user_id})
            data = result.get("result", {})
            return {
                "status": data.get("STATUS", ""),
                "time_start": data.get("TIME_START", ""),
            }
        except Exception as e:
            logger.warning("timeman.status failed for user %s: %s", user_id, e)
            return None

    async def start_work_day(self, user_id: int) -> dict:
        """Start a work day for the user via timeman.open.

        Returns dict with ok, status, time_start, error.
        """
        params = {"USER_ID": user_id}

        try:
            result = await self._request("timeman.open", params)
            data = result.get("result", {})
            return {
                "ok": True,
                "status": data.get("STATUS", "OPENED"),
                "time_start": data.get("TIME_START", ""),
            }
        except Exception as e:
            error_str = str(e)
            logger.error("timeman.open failed for user %s: %s", user_id, e)
            return {"ok": False, "error": error_str}
