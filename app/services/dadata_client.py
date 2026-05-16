"""DaData client — поиск контрагентов по ИНН/названию.

Бесплатный план: 10 000 запросов/сутки. Финансовые поля в выдаче NULL —
для них используем ГИР БО (см. giro_client.py).
"""
import logging

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

BASE_URL = "https://suggestions.dadata.ru/suggestions/api/4_1/rs"


class DaDataClient:
    def __init__(self):
        token = settings.dadata_api_key.get_secret_value().strip()
        if not token:
            self._client = None
            logger.info("DaData: API key not set, disabled")
            return
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=20, write=10, pool=10),
            headers={
                "Authorization": f"Token {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()

    async def find_by_id(self, inn: str) -> dict | None:
        """Точный поиск по ИНН/ОГРН — возвращает основной контрагент."""
        if not self._client:
            return None
        try:
            r = await self._client.post(f"{BASE_URL}/findById/party", json={"query": inn})
            r.raise_for_status()
            sug = (r.json() or {}).get("suggestions") or []
            return sug[0] if sug else None
        except Exception as e:
            logger.warning("DaData findById %s failed: %s", inn, e)
            return None

    async def suggest(self, query: str, count: int = 10) -> list[dict]:
        """Поиск по названию/реквизитам — список подсказок."""
        if not self._client:
            return []
        try:
            r = await self._client.post(
                f"{BASE_URL}/suggest/party",
                json={"query": query, "count": count},
            )
            r.raise_for_status()
            return (r.json() or {}).get("suggestions") or []
        except Exception as e:
            logger.warning("DaData suggest %r failed: %s", query, e)
            return []
