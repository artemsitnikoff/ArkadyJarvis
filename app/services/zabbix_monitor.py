import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db import (
    get_unresolved_zabbix_problems,
    mark_zabbix_resolved,
    set_zabbix_jira_key,
    upsert_zabbix_problem,
)
from app.services.jira_client import JiraClient

logger = logging.getLogger("arkadyjarvis")

# Severities that should escalate to Jira ("warning and above").
# Zabbix uses: Not classified, Information, Warning, Average, High, Disaster.
ESCALATING_SEVERITIES = {"Warning", "Average", "High", "Disaster"}


@dataclass
class ZabbixAlert:
    problem_id: int
    is_open: bool                  # True = 🔴 opened, False = 🟢 resolved
    host: str | None = None
    name: str | None = None
    severity: str | None = None
    raw_text: str = ""


def parse_zabbix_message(text: str) -> ZabbixAlert | None:
    """Parse a Zabbix bot message. Returns None if unrecognized."""
    if not text:
        return None
    head = text.lstrip()
    is_open = head.startswith("🔴")
    is_close = head.startswith("🟢")
    if not (is_open or is_close):
        return None

    pid_match = re.search(r"Original problem ID:\s*(\d+)", text)
    if not pid_match:
        return None

    name_match = re.search(r"Problem name:\s*([^\n]+)", text)
    severity_match = re.search(r"Severity:\s*(\S+)", text)
    host_match = re.search(r"Host:\s*(\S+)", text)

    return ZabbixAlert(
        problem_id=int(pid_match.group(1)),
        is_open=is_open,
        host=host_match.group(1) if host_match else None,
        name=name_match.group(1).strip() if name_match else None,
        severity=severity_match.group(1) if severity_match else None,
        raw_text=text,
    )


async def process_alert_message(text: str, ts: datetime) -> bool:
    """Parse + persist a Zabbix message. Returns True if recognized as Zabbix alert."""
    alert = parse_zabbix_message(text)
    if not alert:
        return False

    iso_ts = ts.astimezone(timezone.utc).isoformat()
    if alert.is_open:
        await upsert_zabbix_problem(
            problem_id=alert.problem_id,
            host=alert.host,
            name=alert.name,
            severity=alert.severity,
            opened_at=iso_ts,
            raw_text=alert.raw_text,
        )
        logger.info(
            "Zabbix: opened problem %s host=%s severity=%s",
            alert.problem_id, alert.host, alert.severity,
        )
    else:
        await mark_zabbix_resolved(alert.problem_id, iso_ts)
        logger.info("Zabbix: resolved problem %s", alert.problem_id)
    return True


async def check_unresolved_and_create_jira(
    jira: JiraClient,
    threshold_hours: int | None = None,
) -> int:
    """Scan unresolved problems older than threshold; create Jira tasks for severity≥Warning.

    Returns number of Jira tasks created.
    """
    threshold = threshold_hours or settings.zabbix_threshold_hours
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=threshold)).isoformat()
    problems = await get_unresolved_zabbix_problems(cutoff)

    created = 0
    for p in problems:
        severity = (p.get("severity") or "").strip()
        if severity not in ESCALATING_SEVERITIES:
            logger.debug(
                "Zabbix: skip problem %s — severity %r below threshold",
                p["problem_id"], severity,
            )
            continue

        host = p.get("host") or "—"
        name = p.get("name") or "Без названия"
        summary = f"[Zabbix] {host} — {name}"[:200]
        description = (
            f"Хост: {host}\n"
            f"Severity: {severity}\n"
            f"Проблема открыта: {p.get('opened_at')}\n"
            f"Висит без закрытия больше {threshold} часов.\n\n"
            f"Original problem ID: {p['problem_id']}\n\n"
            f"--- Исходное сообщение из Zabbix ---\n"
            f"{p.get('raw_text') or ''}"
        )

        try:
            issue = await jira.create_issue(
                project_key=settings.zabbix_jira_project,
                summary=summary,
                description=description,
            )
            issue_key = issue.get("key") if isinstance(issue, dict) else None
            if not issue_key:
                logger.error("Zabbix: Jira create returned no key for problem %s", p["problem_id"])
                continue
            await set_zabbix_jira_key(p["problem_id"], issue_key)
            created += 1
            logger.info(
                "Zabbix: created Jira %s for problem %s (host=%s)",
                issue_key, p["problem_id"], host,
            )
        except Exception as e:
            logger.error(
                "Zabbix: failed to create Jira for problem %s: %s",
                p["problem_id"], e, exc_info=True,
            )

    logger.info("Zabbix daily check: %d Jira tasks created from %d unresolved", created, len(problems))
    return created
