import logging

import httpx

from app.config import settings

logger = logging.getLogger("arkadyjarvis")

# Опциональные поля, которые Jira может отклонить (нет на экране создания
# проекта или нет прав) — их можно выкинуть и пересоздать issue без них.
_OPTIONAL_FIELDS = ("reporter", "assignee")


class JiraClient:
    """Jira client using integration user credentials from settings.

    Usage::

        async with JiraClient() as jira:
            result = await jira.create_issue("DC", "Summary", "Description")
    """

    def __init__(self):
        self._http = httpx.AsyncClient(timeout=30.0)
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

        # Некоторые проекты не выводят опциональные поля (reporter/assignee) на
        # экран создания, либо у интеграционной учётки нет прав их выставлять —
        # тогда Jira 400'ит весь запрос. Выкидываем отклонённое поле и повторяем:
        # issue всё равно создаётся, Jira подставит дефолты (reporter = учётка
        # бота, assignee = дефолт проекта). Цикл конечен — каждое поле дропается
        # не больше раза (droppable требует наличия поля в fields).
        while True:
            result, bad_fields = await self._post_issue(url, fields)
            if result is not None:
                return result
            droppable = [f for f in bad_fields if f in _OPTIONAL_FIELDS and f in fields]
            if not droppable:
                raise RuntimeError("Jira create_issue failed (see log for details)")
            for f in droppable:
                logger.warning("Jira отклонила поле '%s' — повторяю без него", f)
                fields.pop(f, None)

    async def _post_issue(self, url: str, fields: dict) -> tuple[dict | None, list[str]]:
        """POST an issue.

        (result, []) — успех. При 400 на дропаемые опциональные поля возвращает
        (None, [эти поля]), чтобы caller повторил без них. Иначе — raise.
        """
        resp = await self._http.post(
            url,
            json={"fields": fields},
            auth=self._auth,
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code < 400:
            result = resp.json()
            logger.info("Jira issue created: %s", result.get("key"))
            return result, []

        body = resp.text[:500]
        logger.error(
            "Jira create_issue failed: %s %s | payload=%s",
            resp.status_code, body, fields,
        )
        if resp.status_code == 400:
            bad = self._droppable_fields(resp, body)
            if bad:
                return None, bad
        raise RuntimeError(f"Jira {resp.status_code}: {body}")

    @staticmethod
    def _droppable_fields(resp: httpx.Response, body: str) -> list[str]:
        """Какие опциональные поля отклонила Jira. Читаем errors-словарь ответа,
        с фолбэком на легаси-текст 'cannot be assigned' для assignee."""
        bad: list[str] = []
        try:
            errors = resp.json().get("errors", {}) or {}
        except Exception:
            errors = {}
        for f in _OPTIONAL_FIELDS:
            if f in errors:
                bad.append(f)
        if "cannot be assigned" in body and "assignee" not in bad:
            bad.append("assignee")
        return bad

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
