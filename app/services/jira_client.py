import logging

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")


class JiraClient:
    """Jira client using integration user credentials from settings.

    Usage::

        async with JiraClient() as jira:
            result = await jira.create_issue("DC", "Summary", "Description")
    """

    def __init__(self):
        self._http = httpx.AsyncClient()
        self._base_url = settings.jira_url.rstrip("/")
        self._auth = (
            settings.jira_username,
            settings.jira_password.get_secret_value(),
        )

    async def __aenter__(self) -> "JiraClient":
        return self

    async def __aexit__(self, *exc):
        await self._http.aclose()

    async def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str = "",
        reporter_name: str | None = None,
        assignee_name: str | None = None,
    ) -> dict:
        url = f"{self._base_url}/rest/api/2/issue"
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Task"},
        }
        if reporter_name:
            fields["reporter"] = {"name": reporter_name}
        if assignee_name:
            fields["assignee"] = {"name": assignee_name}

        resp = await self._http.post(
            url,
            json={"fields": fields},
            auth=self._auth,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        result = resp.json()

        logger.info("Jira issue created: %s", result.get("key"))
        return result

    async def find_user_by_email(self, email: str) -> str | None:
        """Find Jira username by email address (Jira Server)."""
        url = f"{self._base_url}/rest/api/2/user/search"
        resp = await self._http.get(
            url,
            params={"username": email},
            auth=self._auth,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        users = resp.json()
        if users:
            username = users[0].get("name")
            logger.info("Jira user found by email %s: %s", email, username)
            return username
        return None
