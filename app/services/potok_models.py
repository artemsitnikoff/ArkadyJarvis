from typing import Any

from pydantic import BaseModel


class City(BaseModel):
    id: str | int | None = None
    name: str | None = None
    text: str | None = None

    @property
    def display_name(self) -> str:
        return self.name or self.text or "—"


class ApplicantsCount(BaseModel):
    all: int = 0
    active: int = 0


class Stage(BaseModel):
    id: int
    name: str
    serial: int | None = None
    stage_type: str | None = None


class AjsJoinJob(BaseModel):
    id: int
    name: str
    job_type: str | None = None
    stage_name: str | None = None
    active: bool | None = None


class AjsJoin(BaseModel):
    id: int
    job: AjsJoinJob | None = None
    stage: Stage | None = None


class Job(BaseModel):
    id: int
    name: str
    description: str | None = None
    key_skills: list[str] | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    experience_type: str | None = None
    employment_type: str | None = None
    schedule_type: str | None = None
    city: City | str | None = None
    applicants_count: ApplicantsCount | int | None = None
    state_id: str | None = None

    @property
    def city_name(self) -> str:
        if isinstance(self.city, City):
            return self.city.display_name
        return str(self.city) if self.city else "—"

    @property
    def total_applicants(self) -> int:
        if isinstance(self.applicants_count, ApplicantsCount):
            return self.applicants_count.all
        if isinstance(self.applicants_count, int):
            return self.applicants_count
        return 0


class ExperienceItem(BaseModel):
    company: str | None = None
    position: str | None = None
    description: str | None = None
    start: str | None = None
    end: str | None = None


class EducationPrimary(BaseModel):
    name: str | None = None
    organization: str | None = None
    result: str | None = None
    year: int | None = None


class CvParams(BaseModel):
    title: str | None = None
    experience: Any = None
    education: Any = None
    skills: Any = None
    skill_set: list[str] | None = None
    languages: Any = None
    salary: Any = None
    about_me: str | None = None
    total_experience: Any = None

    model_config = {"extra": "allow"}

    @property
    def all_skills(self) -> list[str]:
        if self.skill_set:
            return self.skill_set
        if isinstance(self.skills, list):
            return [str(s) for s in self.skills]
        return []

    @property
    def experience_items(self) -> list[ExperienceItem]:
        if not isinstance(self.experience, list):
            return []
        result = []
        for item in self.experience:
            if isinstance(item, dict):
                result.append(ExperienceItem.model_validate(item))
            elif isinstance(item, ExperienceItem):
                result.append(item)
        return result

    @property
    def education_list(self) -> list[EducationPrimary]:
        if isinstance(self.education, dict):
            primary = self.education.get("primary", [])
            if isinstance(primary, list):
                return [EducationPrimary.model_validate(e) for e in primary if isinstance(e, dict)]
        return []


class Resume(BaseModel):
    id: int | None = None
    cv_original: str | None = None
    cv_html: str | None = None
    cv_params: CvParams | None = None


class Applicant(BaseModel):
    id: int
    first_name: str | None = None
    last_name: str | None = None
    middle_name: str | None = None
    name: str | None = None
    email: str | None = None
    phones: list[str] | None = None
    salary: str | int | None = None
    title: str | None = None
    city: City | None = None
    resumes: list[Resume] | None = None
    ajs_joins: list[AjsJoin] | None = None
    source_type: str | None = None
    created_at: str | None = None

    model_config = {"extra": "allow"}

    @property
    def display_name(self) -> str:
        if self.name:
            return self.name
        return " ".join(filter(None, [self.last_name, self.first_name, self.middle_name])) or f"ID:{self.id}"


class ScoreBreakdown(BaseModel):
    criterion: str
    score: int
    comment: str = ""


class ScoringResult(BaseModel):
    applicant_id: int
    applicant_name: str
    score: int
    reasoning: str
    strengths: list[str]
    weaknesses: list[str]
    breakdown: list[ScoreBreakdown] = []
