import asyncio
import html as html_mod
import json
import logging
import re

import httpx

from app.config import settings
from app.services.potok_models import Applicant, Job, ScoringResult

logger = logging.getLogger("arkadyjarvis")


def score_label(score: int) -> str:
    if score >= 81:
        return "Отлично"
    if score >= 61:
        return "Хорошо"
    if score >= 41:
        return "Средне"
    return "Слабо"


def _build_comment_html(result: ScoringResult) -> str:
    """Render Potok event comment HTML for a scoring result (all user-supplied
    strings are html-escaped)."""
    esc = html_mod.escape
    label = score_label(result.score)

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

    questions_html = ""
    if result.questions:
        questions_items = "".join(f"<li>{esc(q)}</li>" for q in result.questions)
        questions_json = json.dumps(result.questions, ensure_ascii=False)
        questions_html = (
            f"<!-- JARVIS:QUESTIONS:{questions_json} -->"
            f"<br><b>💬 Вопросы для первого контакта:</b>"
            f"<ul>{questions_items}</ul>"
        )

    return (
        f"<h3>🤖 Оценка AI: {result.score}/100 ({label})</h3>"
        f"<p>{esc(result.reasoning)}</p>"
        f"{breakdown_html}"
        f"<br>"
        f"<b>✅ Сильные стороны:</b>"
        f"<ul>{strengths}</ul>"
        f"<b>⚠️ Слабые стороны:</b>"
        f"<ul>{weaknesses}</ul>"
        f"{questions_html}"
    )


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


def _parse_retry_after(header_value: str | None, default: float = 2.0) -> float:
    """Potok's Retry-After is usually a number of seconds, but RFC7231 also allows
    an HTTP-date. Only the numeric form is actionable; fall back to `default`
    otherwise. Never raises."""
    if not header_value:
        return default
    try:
        return float(header_value)
    except ValueError:
        return default


class PotokClient:
    def __init__(self):
        token = settings.potok_api_token.get_secret_value()
        self._client = httpx.AsyncClient(
            base_url=settings.potok_base_url,
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        # Cap concurrent applicant fetches — Potok rate-limits aggressively,
        # and parallel score_new/rescore_all flows would otherwise double the
        # load on the /applicants/{id} endpoint. Instance-level so the
        # semaphore is tied to the client's event loop.
        self._fetch_semaphore = asyncio.Semaphore(5)
        # questions_cache: populated during push_scoring, read at contact time
        self._questions_cache: dict[int, list[str]] = {}

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
        async with self._fetch_semaphore:
            for attempt in range(retries + 1):
                resp = await self._client.get(f"/api/v3/applicants/{applicant_id}.json")
                if resp.status_code == 429 and attempt < retries:
                    delay = _parse_retry_after(resp.headers.get("Retry-After"))
                    await asyncio.sleep(delay)
                    continue
                # Either success, any non-429 error, or 429 on the last attempt:
                # let raise_for_status surface it to the caller.
                resp.raise_for_status()
                return resp.json()
            # Unreachable — loop either returns or raises, but keep an explicit
            # raise so linters / future refactors don't invent an implicit None.
            raise RuntimeError(
                f"Potok: applicant {applicant_id} retry loop exited without response"
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
        if result.questions:
            self._questions_cache[result.applicant_id] = result.questions

        comment = _build_comment_html(result)
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
            try:
                resp = await self._client.patch(
                    f"/api/v3/applicants/{result.applicant_id}.json",
                    json={"applicant": {"last_name": new_last_name}},
                )
                resp.raise_for_status()
            except Exception as e:
                # Comment is already posted; prefix update failing is a
                # non-fatal inconsistency. On rescore_all a duplicate comment
                # may appear until the prefix catches up — log loudly so ops
                # can spot it, but don't re-raise.
                logger.warning(
                    "Potok: posted comment for applicant %s (score %d) but "
                    "failed to update last_name prefix: %s",
                    result.applicant_id, result.score, e,
                )

    async def get_applicant_questions(self, applicant_id: int) -> list[str]:
        """Return AI-generated interview questions for an applicant.

        Checks the in-memory cache first (populated during push_scoring in the
        same process lifetime), then falls back to fetching Potok events.
        """
        if applicant_id in self._questions_cache:
            logger.info("Potok: questions for %s served from cache", applicant_id)
            return self._questions_cache[applicant_id]

        # Fallback: parse from Potok applicant detail (events embedded in response)
        try:
            resp = await self._client.get(f"/api/v3/applicants/{applicant_id}.json")
            resp.raise_for_status()
            data = resp.json()
            events = data.get("events", [])
            logger.info("Potok: fetched %d events for applicant %s", len(events), applicant_id)
        except Exception as e:
            logger.warning("Potok: failed to fetch events for applicant %s: %s", applicant_id, e)
            return []

        for event in reversed(events):
            body = event.get("body", "")
            match = re.search(r"<!-- JARVIS:QUESTIONS:(.*?) -->", body, re.DOTALL)
            if match:
                try:
                    questions = json.loads(match.group(1))
                    self._questions_cache[applicant_id] = questions
                    return questions
                except Exception as e:
                    logger.warning("Potok: failed to parse questions JSON: %s", e)

        logger.warning("Potok: no questions found for applicant %s", applicant_id)
        return []
