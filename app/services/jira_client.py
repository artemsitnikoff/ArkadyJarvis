import logging

import httpx

from app import db

logger = logging.getLogger("arkadyjarvis")


class JiraClient:
    """Per-user Jira client. Use as async context manager.

    Usage::

        async with JiraClient(telegram_id) as jira:
            result = await jira.create_issue("DC", "Summary", "Description")
    """

    def __init__(self, telegram_id: int):
        self._telegram_id = telegram_id
        self._http = httpx.AsyncClient()
        self._creds: dict | None = None

    async def __aenter__(self) -> "JiraClient":
        self._creds = await db.get_jira_credentials(self._telegram_id)
        if self._creds is None:
            raise RuntimeError("Jira не настроена. Выполни /jira")
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def create_issue(
        self, project_key: str, summary: str, description: str = ""
    ) -> dict:
        url = f"{self._creds['jira_url'].rstrip('/')}/rest/api/2/issue"
        payload = {
            "fields": {
                "project": {"key": project_key},
                "summary": summary,
                "description": description,
                "issuetype": {"name": "Task"},
            }
        }

        resp = await self._http.post(
            url,
            json=payload,
            auth=(self._creds["jira_username"], self._creds["jira_password"]),
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()

        logger.info("Jira issue created: %s (by tg=%s)", result.get("key"), self._telegram_id)
        return result
