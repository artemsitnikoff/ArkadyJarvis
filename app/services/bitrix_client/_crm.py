import logging

logger = logging.getLogger("arkadyjarvis")


class _BitrixCRMMixin:
    """CRM-related Bitrix24 methods."""

    async def create_lead(self, fields: dict) -> dict:
        result = await self._request("crm.lead.add", {"fields": fields})
        lead_id = result.get("result")
        logger.info("Bitrix lead created: id=%s title=%s", lead_id, fields.get("TITLE"))
        return {"status": "ok", "id": lead_id}

    async def add_timeline_comment(
        self, entity_id: int, entity_type: str, comment: str,
        author_id: int | None = None,
    ) -> int | None:
        """Добавляет комментарий в таймлайн сущности CRM (правая колонка карточки).
        entity_type: 'lead' | 'deal' | 'contact' | 'company'. BBCODE поддерживается."""
        fields = {
            "ENTITY_ID": entity_id,
            "ENTITY_TYPE": entity_type,
            "COMMENT": comment,
        }
        if author_id:
            fields["AUTHOR_ID"] = author_id
        r = await self._request("crm.timeline.comment.add", {"fields": fields})
        cid = r.get("result")
        logger.info(
            "Bitrix timeline comment added: id=%s entity=%s/%s len=%d",
            cid, entity_type, entity_id, len(comment),
        )
        return int(cid) if cid else None
