import asyncio
import html as html_mod
import logging
import re

import httpx

from app.config import settings
from app.services.potok_models import Applicant, Job, ScoringResult

# Limit concurrent applicant fetches across the whole client — Potok
# rate-limits aggressively, and parallel score_new/rescore_all flows could
# otherwise double the load on the /applicants/{id} endpoint.
_FETCH_SEMAPHORE = asyncio.Semaphore(5)

logger = logging.getLogger("arkadyjarvis")


def _strip_html(html: str) -> str:
    """Convert HTML to readable plain text preserving structure."""
    text = html
    # Line breaks
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    # Block elements → newlines
    text = re.sub(r"</(p|div|h[1-6]|tr|table)>", "\n", text, flags=re.IGNORECASE)
    text = re.sub(r"<(p|div|h[1-6]|tr|table|thead|tbody)\b[^>]*>", "\n", text, flags=re.IGNORECASE)
    # List items → bullets
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"</li>", "", text, flags=re.IGNORECASE)
    # Remove remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Decode common entities
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = text.replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&quot;", '"').replace("&laquo;", "«").replace("&raquo;", "»")
    # Collapse spaces on same line (but keep newlines)
    text = re.sub(r"[^\S\n]+", " ", text)
    # Strip each line
    lines = [line.strip() for line in text.split("\n")]
    # Collapse 3+ blank lines into 2
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
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

    async def _get_job_applicant_ids(self, job_id: int) -> list[int]:
        """Get ALL applicant IDs for a job via /jobs/{id}/ajs_joins.json (cursor pagination)."""
        ids: list[int] = []
        cursor = None
        while True:
            params: dict = {"per_page": 100}
            if cursor:
                params["page_cursor"] = cursor
            resp = await self._client.get(
                f"/api/v3/jobs/{job_id}/ajs_joins.json", params=params,
            )
            resp.raise_for_status()
            data = resp.json()
            for obj in data.get("objects", []):
                ids.append(obj["applicant_id"])
            if not data.get("has_next_page"):
                break
            cursor = data.get("page_next_cursor")
        logger.info("Potok: job %s has %d applicant IDs via ajs_joins", job_id, len(ids))
        return ids

    async def _fetch_applicant(self, applicant_id: int, retries: int = 3) -> dict:
        """Fetch single applicant by ID with retry on 429. Concurrency-limited."""
        async with _FETCH_SEMAPHORE:
            last_status = None
            for attempt in range(retries + 1):
                resp = await self._client.get(f"/api/v3/applicants/{applicant_id}.json")
                last_status = resp.status_code
                if resp.status_code == 429:
                    if attempt < retries:
                        delay = float(resp.headers.get("Retry-After", 2))
                        await asyncio.sleep(delay)
                        continue
                resp.raise_for_status()
                return resp.json()
            raise RuntimeError(
                f"Potok: applicant {applicant_id} unreachable after {retries + 1} "
                f"attempts (last status {last_status})"
            )

    async def get_applicants_for_job(
        self,
        job_id: int,
        limit: int = 20,
        skip_scored: bool = True,
    ) -> list[Applicant]:
        """Get applicants for a job.

        Uses /jobs/{id}/ajs_joins.json to get all applicant IDs (no pagination limit),
        then fetches each applicant's details in parallel batches.
        """
        applicant_ids = await self._get_job_applicant_ids(job_id)

        found: list[Applicant] = []
        batch_size = 5

        for i in range(0, len(applicant_ids), batch_size):
            batch_ids = applicant_ids[i : i + batch_size]
            tasks = [self._fetch_applicant(aid) for aid in batch_ids]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for aid, data in zip(batch_ids, results):
                if isinstance(data, Exception):
                    logger.error("Potok: failed to fetch applicant %s: %s", aid, data)
                    continue
                item_name = f"{data.get('last_name', '')} {data.get('first_name', '')}".strip()
                if skip_scored and re.match(r"^\d{3}-", data.get("last_name") or ""):
                    logger.info("Potok: skip scored %s", item_name)
                    continue
                found.append(Applicant.model_validate(data))
                logger.info("Potok: found %s (id=%s)", item_name, aid)
                if limit and len(found) >= limit:
                    return found[:limit]

            if i + batch_size < len(applicant_ids):
                await asyncio.sleep(0.5)

        logger.info(
            "Potok: job_id=%s, found %d candidates (skip_scored=%s)",
            job_id, len(found), skip_scored,
        )
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

        esc = html_mod.escape
        breakdown_html = ""
        if result.breakdown:
            rows = ""
            for b in result.breakdown:
                rows += (
                    f"<tr>"
                    f"<td style='padding:4px 8px'>{esc(b.criterion)}</td>"
                    f"<td style='padding:4px 8px;text-align:center'><b>{b.score}</b></td>"
                    f"<td style='padding:4px 8px'>{esc(b.comment)}</td>"
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

        strengths = (
            "".join(f"<li>{esc(s)}</li>" for s in result.strengths)
            if result.strengths else "<li>нет</li>"
        )
        weaknesses = (
            "".join(f"<li>{esc(s)}</li>" for s in result.weaknesses)
            if result.weaknesses else "<li>нет</li>"
        )

        comment = (
            f"<h3>🤖 Оценка AI: {result.score}/100 ({label})</h3>"
            f"<p>{esc(result.reasoning)}</p>"
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
            clean_name = re.sub(r"^(\d{3}-)+", "", original_last_name)
            new_last_name = f"{result.score:03d}-{clean_name}"
            resp = await self._client.patch(
                f"/api/v3/applicants/{result.applicant_id}.json",
                json={"applicant": {"last_name": new_last_name}},
            )
            resp.raise_for_status()
