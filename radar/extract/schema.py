from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

RoleFamily = Literal[
    "backend", "frontend", "fullstack", "mobile", "data", "ml", "platform", "embedded", "simulation", "security", "qa",
    "product", "design", "it_support", "solutions", "other",
]
# "trainee" = paid entry programme (traineeship, graduate programme, young-professional / talent programme);
# "intern" = internship, graduation project or working-student job;
# "phd" = a doctoral position (PhD candidate, promovendus, AIO); "postdoc" = a postdoctoral research position.
Seniority = Literal["intern", "trainee", "phd", "postdoc", "junior", "medior", "senior", "lead", "staff", "manager",
                    "unknown"]
RemotePolicy = Literal["remote", "hybrid", "onsite", "unknown"]
Degree = Literal["phd", "msc", "bsc", "hbo", "mbo", "none", "unknown"]


class Extraction(BaseModel):
    """Structured facts extracted from one posting. Every extractor (rules, LLM) fills this schema."""

    role_family: RoleFamily = "other"
    seniority: Seniority = "unknown"
    skills_required: list[str] = Field(default_factory=list)
    skills_nice: list[str] = Field(default_factory=list)
    posting_language: Literal["en", "nl", "other"] = "en"
    dutch_required: bool = False
    english_only: bool = True  # no Dutch required
    english_required: bool = True  # posted in English or asks for English; False = Dutch is enough
    years_experience: int | None = None
    years_experience_text: str | None = None  # as worded: "1-3", "5+", "1.5", "3"
    years_preferred: bool = False  # the years are a preference ("ideally 1 year", "is a plus"), not a requirement
    # work experience required without a number: "proven experience as a developer", "werkervaring", "Berufserfahrung"
    work_experience_required: bool = False
    salary_min_eur: int | None = None
    salary_max_eur: int | None = None
    visa_sponsorship: bool | None = None
    remote_policy: RemotePolicy = "unknown"
    degree_required: Degree = "unknown"
    # Internships: True when the posting requires being enrolled at a university or school for the duration,
    # False when it says graduates are welcome or enrolment is not needed, None when it does not say.
    enrollment_required: bool | None = None
    # the text welcomes people without work experience: "recent graduates", "entry-level", "no experience required",
    # "starters op de arbeidsmarkt" (radar/extract/rules.py, is_entry_level)
    welcomes_starters: bool = False

    def all_skills(self) -> list[str]:
        seen: dict[str, None] = {}
        for s in self.skills_required + self.skills_nice:
            seen.setdefault(s, None)
        return list(seen)
