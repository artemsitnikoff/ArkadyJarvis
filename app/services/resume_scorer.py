import re
from typing import TYPE_CHECKING

from app.services.potok_models import Applicant, Job, ScoringResult
from app.utils import parse_json_response

if TYPE_CHECKING:
    from app.services.ai_client import AIClient

SCORING_PROMPT = """Ты — эксперт по подбору персонала в IT-компании. Оцени, насколько кандидат подходит под вакансию.

## Вакансия
Название: {job_name}
Описание: {job_description}
Ключевые навыки: {job_skills}
Зарплатная вилка: {job_salary}
Требуемый опыт: {job_experience}

## Кандидат
Имя: {applicant_name}
Должность: {resume_title}
Зарплатные ожидания: {applicant_salary}
Город: {applicant_city}

### Опыт работы:
{experience}

### Образование:
{education}

### Навыки:
{skills}

### О себе:
{about_me}

## Задача
Оцени кандидата по шкале от 0 до 100. Раздели оценку на критерии — выдели ключевые навыки и требования из вакансии и оцени кандидата по каждому отдельно. Сумма баллов по критериям = итоговый балл.

Примерные категории критериев (адаптируй под конкретную вакансию):
- Ключевые технические навыки (каждый важный навык отдельно)
- Релевантный опыт работы (годы, должности)
- Стабильность (частота смены работы)
- Образование
- Локация / готовность к переезду
- Зарплатные ожидания vs вилка вакансии
{recruiter_instructions}
Ответь СТРОГО в формате JSON (без markdown, без ```):
{{
  "score": <число 0-100>,
  "reasoning": "<краткое обоснование на русском, 1-2 предложения>",
  "breakdown": [
    {{"criterion": "<название критерия>", "score": <баллы>, "comment": "<почему столько>"}},
    ...
  ],
  "strengths": ["<сильная сторона 1>", ...],
  "weaknesses": ["<слабая сторона 1>", ...]
}}"""


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


def _build_prompt(job: Job, applicant: Applicant) -> str:
    cv_params = None
    if applicant.resumes:
        cv_params = applicant.resumes[0].cv_params

    raw_desc = job.description or "Не указано"
    clean_desc, instructions = extract_recruiter_instructions(raw_desc)

    recruiter_block = ""
    if instructions:
        recruiter_block = f"\n\n## ОСОБЫЕ УКАЗАНИЯ РЕКРУТЕРА (обязательно учти!):\n{instructions}\n"

    return SCORING_PROMPT.format(
        job_name=job.name,
        job_description=clean_desc,
        recruiter_instructions=recruiter_block,
        job_skills=", ".join(job.key_skills) if job.key_skills else "Не указаны",
        job_salary=f"{job.salary_from or '?'} — {job.salary_to or '?'}"
        if job.salary_from or job.salary_to
        else "Не указана",
        job_experience=job.experience_type or "Не указан",
        applicant_name=applicant.display_name,
        resume_title=applicant.title
        or (cv_params.title if cv_params else None)
        or "Не указан",
        applicant_salary=applicant.salary
        or (cv_params.salary if cv_params else None)
        or "Не указана",
        applicant_city=applicant.city.display_name if applicant.city else "Не указан",
        experience=_format_experience(cv_params),
        education=_format_education(cv_params),
        skills=_format_skills(cv_params),
        about_me=(cv_params.about_me or "Не указано")[:500] if cv_params else "Не указано",
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
    )
