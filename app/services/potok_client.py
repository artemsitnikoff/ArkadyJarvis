import asyncio
import logging
import re

import httpx

from app.config import settings
from app.services.potok_models import Applicant, Job, ScoringResult

logger = logging.getLogger("arkadyjarvis")


def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class PotokClient:
    def __init__(self):
        token = settings.potok_api_token.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=settings.potok_base_url,
            timeout=30.0,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )

    async def close(self):
        await self._client.aclose()

    async def get_jobs(self, scope: str = "active") -> list[Job]:
        resp = await self._client.get(
            "/api/v3/cursor_paginated/jobs.json",
            params={"by_scope": scope, "per_page": 50},
        )
        resp.raise_for_status()
        data = resp.json()
        jobs_data = data.get("objects", {}).get("jobs", [])
        return [Job.model_validate(j) for j in jobs_data]

    async def get_job(self, job_id: int) -> Job:
        resp = await self._client.get(f"/api/v2/jobs/{job_id}.json")
        resp.raise_for_status()
        data = resp.json()
        if data.get("description"):
            data["description"] = _strip_html(data["description"])
        return Job.model_validate(data)

    async def _fetch_page(self, page: int) -> dict:
        resp = await self._client.get(
            "/api/v3/applicants",
            params={"per_page": 100, "page": page},
        )
        resp.raise_for_status()
        return resp.json()

    async def get_applicants_for_job(
        self,
        job_id: int,
        limit: int = 20,
        skip_scored: bool = True,
    ) -> list[Applicant]:
        found: list[Applicant] = []
        batch_size = 10

        first = await self._fetch_page(1)
        total_pages = first.get("pages", 1)

        for item in first.get("data", []):
            if skip_scored and re.match(r"^\d{3}-", item.get("last_name") or ""):
                continue
            for aj in item.get("ajs_joins", []):
                if aj.get("job", {}).get("id") == job_id:
                    found.append(Applicant.model_validate(item))
                    break
            if limit and len(found) >= limit:
                return found[:limit]

        page = 2
        while page <= total_pages:
            batch_end = min(page + batch_size, total_pages + 1)
            tasks = [self._fetch_page(p) for p in range(page, batch_end)]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for data in results:
                if isinstance(data, Exception):
                    continue
                for item in data.get("data", []):
                    if skip_scored and re.match(r"^\d{3}-", item.get("last_name") or ""):
                        continue
                    for aj in item.get("ajs_joins", []):
                        if aj.get("job", {}).get("id") == job_id:
                            found.append(Applicant.model_validate(item))
                            break
                    if limit and len(found) >= limit:
                        return found[:limit]

            page = batch_end

        return found

    async def push_scoring(
        self, result: ScoringResult, job_id: int, original_last_name: str = ""
    ) -> None:
        label = (
            "Отлично" if result.score >= 81
            else "Хорошо" if result.score >= 61
            else "Средне" if result.score >= 41
            else "Слабо"
        )

        breakdown_html = ""
        if result.breakdown:
            rows = ""
            for b in result.breakdown:
                rows += (
                    f"<tr>"
                    f"<td style='padding:4px 8px'>{b.criterion}</td>"
                    f"<td style='padding:4px 8px;text-align:center'><b>{b.score}</b></td>"
                    f"<td style='padding:4px 8px'>{b.comment}</td>"
                    f"</tr>"
                )
            breakdown_html = (
                f"<br><b>📊 Разбивка по критериям:</b>"
                f"<table border='1' cellpadding='0' cellspacing='0' "
                f"style='border-collapse:collapse;margin-top:5px;width:100%'>"
                f"<tr style='background:#f0f0f0'>"
                f"<th style='padding:4px 8px;text-align:left;width:30%'>Критерий</th>"
                f"<th style='padding:4px 8px;text-align:center;width:60px'>Баллы</th>"
                f"<th style='padding:4px 8px;text-align:left'>Комментарий</th>"
                f"</tr>"
                f"{rows}"
                f"<tr style='background:#f0f0f0'>"
                f"<td style='padding:4px 8px'><b>ИТОГО</b></td>"
                f"<td style='padding:4px 8px;text-align:center'><b>{result.score}</b></td>"
                f"<td style='padding:4px 8px'></td>"
                f"</tr>"
                f"</table>"
            )

        strengths = "".join(f"<li>{s}</li>" for s in result.strengths) if result.strengths else "<li>нет</li>"
        weaknesses = "".join(f"<li>{s}</li>" for s in result.weaknesses) if result.weaknesses else "<li>нет</li>"

        comment = (
            f"<h3>🤖 Оценка AI: {result.score}/100 ({label})</h3>"
            f"<p>{result.reasoning}</p>"
            f"{breakdown_html}"
            f"<br>"
            f"<b>✅ Сильные стороны:</b>"
            f"<ul>{strengths}</ul>"
            f"<b>⚠️ Слабые стороны:</b>"
            f"<ul>{weaknesses}</ul>"
        )

        event = {
            "applicant_id": result.applicant_id,
            "body": comment,
            "type": "Event::Comment",
            "job_id": job_id,
        }
        resp = await self._client.post(
            "/api/v3/events.json",
            json={"event": event},
        )
        resp.raise_for_status()

        if original_last_name:
            clean_name = re.sub(r"^\d{3}-", "", original_last_name)
            new_last_name = f"{result.score:03d}-{clean_name}"
            resp = await self._client.patch(
                f"/api/v3/applicants/{result.applicant_id}.json",
                json={"applicant": {"last_name": new_last_name}},
            )
            resp.raise_for_status()
