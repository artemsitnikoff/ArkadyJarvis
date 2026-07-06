import re
from typing import TYPE_CHECKING

from app.services.potok_models import Applicant, Job, ScoringResult
from app.services.prompts import load_prompt
from app.utils import parse_json_response

if TYPE_CHECKING:
    from app.services.ai_client import AIClient

# Промт скоринга вынесен в prompts/recruiter_scoring.md — редактируется как файл,
# как остальные промты проекта. Плейсхолдеры ({job_name}, {recruiter_instructions}
# и т.д.) подставляются в _build_prompt одним regex-проходом, поэтому литеральные
# фигурные скобки JSON-примера в .md одинарные (без .format()-экранирования {{ }}).
SCORING_PROMPT = load_prompt("recruiter_scoring")

# {placeholder} -> значение; неизвестный плейсхолдер остаётся как есть (m.group(0)).
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _format_experience(cv_params) -> str:
    if not cv_params:
        return "Не указан"
    items = cv_params.experience_items
    if not items:
        return "Не указан"
    lines = []
    for exp in items:
        period = f"{exp.start or '?'} — {exp.end or 'по настоящее время'}"
        company = exp.company or "?"
        position = exp.position or "?"
        lines.append(f"- {company}, {position} ({period})")
        if exp.description:
            lines.append(f"  {exp.description[:500]}")
    return "\n".join(lines) or "Не указан"


def _format_education(cv_params) -> str:
    if not cv_params:
        return "Не указано"
    edu_list = cv_params.education_list
    if not edu_list:
        return "Не указано"
    lines = []
    for edu in edu_list:
        name = edu.name or "?"
        org = edu.organization or ""
        result = edu.result or ""
        year = edu.year or "?"
        lines.append(f"- {name} {org} — {result} ({year})")
    return "\n".join(lines) or "Не указано"


def _format_skills(cv_params) -> str:
    if not cv_params:
        return "Не указаны"
    skills = cv_params.all_skills
    return ", ".join(skills) if skills else "Не указаны"


def extract_recruiter_instructions(description: str) -> tuple[str, str]:
    if not description:
        return description, ""
    match = re.search(r"(?:Важно для CLAUDE[:\s])(.*)", description, re.DOTALL | re.IGNORECASE)
    if match:
        instructions = match.group(1).strip()
        clean_desc = description[:match.start()].strip()
        return clean_desc, instructions
    return description, ""


def _strip_admin_lines(description: str) -> str:
    """Remove internal metadata lines (Владельцы, Ссылка для встречи) so they
    don't leak into the scoring prompt and waste tokens."""
    lines = description.split("\n")
    cleaned = [
        ln for ln in lines
        if not re.match(r"^\s*(Владельцы|Ссылка для встречи)\s*:", ln, re.IGNORECASE)
    ]
    return "\n".join(cleaned).strip()


def _build_prompt(job: Job, applicant: Applicant) -> str:
    cv_params = None
    if applicant.resumes:
        cv_params = applicant.resumes[0].cv_params

    raw_desc = job.description or "Не указано"
    clean_desc, instructions = extract_recruiter_instructions(raw_desc)
    clean_desc = _strip_admin_lines(clean_desc)

    recruiter_block = ""
    if instructions:
        recruiter_block = f"\n\n## ОСОБЫЕ УКАЗАНИЯ РЕКРУТЕРА (обязательно учти!):\n{instructions}\n"

    values = {
        "job_name": job.name,
        "job_description": clean_desc,
        "recruiter_instructions": recruiter_block,
        "job_skills": ", ".join(job.key_skills) if job.key_skills else "Не указаны",
        "job_salary": f"{job.salary_from or '?'} — {job.salary_to or '?'}"
        if job.salary_from or job.salary_to
        else "Не указана",
        "job_experience": job.experience_type or "Не указан",
        "applicant_name": applicant.display_name,
        "resume_title": applicant.title
        or (cv_params.title if cv_params else None)
        or "Не указан",
        "applicant_salary": applicant.salary
        or (cv_params.salary if cv_params else None)
        or "Не указана",
        "applicant_city": applicant.city.display_name if applicant.city else "Не указан",
        "experience": _format_experience(cv_params),
        "education": _format_education(cv_params),
        "skills": _format_skills(cv_params),
        "about_me": (cv_params.about_me or "Не указано")[:500] if cv_params else "Не указано",
    }
    return _PLACEHOLDER_RE.sub(
        lambda m: str(values.get(m.group(1), m.group(0))), SCORING_PROMPT
    )


def _parse_response(text: str) -> dict:
    return parse_json_response(text)


async def score_applicant(
    job: Job, applicant: Applicant, *, ai_client: "AIClient",
) -> ScoringResult:
    prompt = _build_prompt(job, applicant)
    response_text = await ai_client.complete(prompt, timeout=300)
    result = _parse_response(response_text)

    return ScoringResult(
        applicant_id=applicant.id,
        applicant_name=applicant.display_name,
        score=result["score"],
        reasoning=result["reasoning"],
        strengths=result.get("strengths", []),
        weaknesses=result.get("weaknesses", []),
        breakdown=result.get("breakdown", []),
        questions=result.get("questions", []),
    )
