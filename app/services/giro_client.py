"""ГИР БО ФНС — бесплатный публичный API бухгалтерской отчётности.

https://bo.nalog.gov.ru/ — официальный гос. ресурс. По закону все
организации сдают сюда годовую отчётность (форма 1: баланс, форма 2:
отчёт о финансовых результатах).
"""
import logging

import httpx

logger = logging.getLogger("arkadyjarvis")

BASE_URL = "https://bo.nalog.gov.ru"


class GiroClient:
    def __init__(self):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=10, read=30, write=10, pool=10),
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 (compatible; ArkadyJarvis/1.0)",
            },
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def find_org_id(self, inn: str) -> int | None:
        """Поиск ID организации по ИНН в реестре БФО."""
        try:
            r = await self._client.get(
                f"{BASE_URL}/advanced-search/organizations/search",
                params={"query": inn, "page": "0"},
            )
            r.raise_for_status()
            content = (r.json() or {}).get("content") or []
            return content[0].get("id") if content else None
        except Exception as e:
            logger.warning("GIRO find_org_id %s failed: %s", inn, e)
            return None

    async def get_summary(self, inn: str) -> dict:
        """Сводка финансовой отчётности: выручка и активы по годам.

        Возвращает {'years': [{'period', 'revenue', 'assets', 'reported_at'}, …]}.
        Если организация не найдена в БФО — пустой dict.
        """
        org_id = await self.find_org_id(inn)
        if not org_id:
            return {}
        try:
            r = await self._client.get(f"{BASE_URL}/nbo/organizations/{org_id}")
            r.raise_for_status()
            data = r.json() or {}
        except Exception as e:
            logger.warning("GIRO get_summary %s failed: %s", inn, e)
            return {}

        years = []
        for bfo in (data.get("bfo") or []):
            period = bfo.get("period")
            if not period:
                continue
            years.append({
                "period": period,
                "revenue": bfo.get("gainSum"),     # ₽ × 1000 (тысячи рублей)
                "assets": bfo.get("actives"),
                "reported_at": bfo.get("actualBfoDate"),
            })
        years.sort(key=lambda x: x["period"], reverse=True)
        return {
            "org_id": org_id,
            "full_name": data.get("shortName"),
            "okved": (data.get("okved2") or {}).get("name"),
            "years": years,
        }
