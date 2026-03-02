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
        reporter_account_id: str | None = None,
        assignee_account_id: str | None = None,
    ) -> dict:
        url = f"{self._base_url}/rest/api/2/issue"
        fields = {
            "project": {"key": project_key},
            "summary": summary,
            "description": description,
            "issuetype": {"name": "Task"},
        }
        if reporter_account_id:
            fields["reporter"] = {"accountId": reporter_account_id}
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}

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
        """Find Jira accountId by email address."""
        url = f"{self._base_url}/rest/api/2/user/search"
        resp = await self._http.get(
            url,
            params={"query": email},
            auth=self._auth,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()
        users = resp.json()
        if users:
            account_id = users[0].get("accountId")
            logger.info("Jira user found by email %s: %s", email, account_id)
            return account_id
        return None
