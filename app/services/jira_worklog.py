"""Jira worklog fetcher для Мисис Хадсон.

Тянет worklog-записи указанных авторов за период через JQL `worklogAuthor in
(...)` + per-issue /worklog для деталей (т.к. в /search?fields=worklog
возвращаются только первые 20 записей на issue).
"""
import logging
from dataclasses import dataclass
from datetime import date, datetime

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


@dataclass
class WorklogEntry:
    issue_key: str
    project_key: str
    issue_summary: str
    author: str
    started_at: datetime
    time_spent_seconds: int
    comment: str

    @property
    def hours(self) -> float:
        return self.time_spent_seconds / 3600


async def fetch_worklogs(
    jira_authors: list[str],
    since: date,
    until: date,
    project_keys: set[str] | None = None,
) -> list[WorklogEntry]:
    """Возвращает все worklog-записи указанных авторов в диапазоне [since, until]
    (включительно по дате). Опционально фильтрует по project_keys.

    JQL: worklogAuthor in ("a@x", ...) AND worklogDate >= "YYYY-MM-DD"
         AND worklogDate <= "YYYY-MM-DD"
    """
    if not jira_authors:
        return []

    base = settings.jira_url.rstrip("/")
    auth = (settings.jira_username, settings.jira_password.get_secret_value())
    authors_jql = ", ".join(f'"{a}"' for a in jira_authors)
    jql = (
        f"worklogAuthor in ({authors_jql}) "
        f'AND worklogDate >= "{since.isoformat()}" '
        f'AND worklogDate <= "{until.isoformat()}"'
    )
    logger.info("Jira worklog JQL: %s", jql[:200])

    author_set = set(jira_authors)
    issues: list[dict] = []
    start_at = 0
    async with httpx.AsyncClient(timeout=60.0, auth=auth) as http:
        while True:
            r = await http.get(
                f"{base}/rest/api/2/search",
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": 100,
                    "fields": "summary,project",
                },
            )
            r.raise_for_status()
            data = r.json()
            batch = data.get("issues", [])
            issues.extend(batch)
            total = data.get("total", 0)
            start_at += len(batch)
            if start_at >= total or not batch:
                break
        logger.info("Jira: %d issues matched", len(issues))

        entries: list[WorklogEntry] = []
        for issue in issues:
            ikey = issue["key"]
            project_key = issue["fields"]["project"]["key"]
            if project_keys is not None and project_key not in project_keys:
                continue
            summary = (issue["fields"].get("summary") or "").strip()

            wr = await http.get(f"{base}/rest/api/2/issue/{ikey}/worklog")
            wr.raise_for_status()
            for wl in wr.json().get("worklogs", []):
                author = (wl.get("author") or {}).get("name")
                if author not in author_set:
                    continue
                started_raw = wl.get("started")
                if not started_raw:
                    continue
                try:
                    started_dt = datetime.fromisoformat(
                        started_raw.replace("Z", "+00:00")
                    )
                except ValueError:
                    continue
                if not (since <= started_dt.date() <= until):
                    continue
                entries.append(
                    WorklogEntry(
                        issue_key=ikey,
                        project_key=project_key,
                        issue_summary=summary,
                        author=author,
                        started_at=started_dt,
                        time_spent_seconds=int(wl.get("timeSpentSeconds") or 0),
                        comment=(wl.get("comment") or "").strip(),
                    )
                )
    logger.info("Worklog entries collected: %d", len(entries))
    return entries
