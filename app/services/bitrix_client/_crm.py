import logging

logger = logging.getLogger("arkadyjarvis")


class _BitrixCRMMixin:
    """CRM-related Bitrix24 methods."""

    async def create_lead(self, fields: dict) -> dict:
        result = await self._request("crm.lead.add", {"fields": fields})
        lead_id = result.get("result")
        logger.info("Bitrix lead created: id=%s title=%s", lead_id, fields.get("TITLE"))
        return {"status": "ok", "id": lead_id}
