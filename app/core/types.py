from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(slots=True)
class ResumeProfile:
    id: int | None
    filename: str
    file_path: str
    file_hash: str
    raw_text: str
    summary_text: str
    skills: list[str]
    tools: list[str]
    certifications: list[str]
    clearance_terms: list[str]
    recent_titles: list[str]
    experience_years: float
    experience_spans: list[dict[str, str]]
    sections: dict[str, str]
    application_profile: dict[str, Any] = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class JobSourceConfig:
    id: int | None
    name: str
    source_type: str
    url: str
    identifier: str | None = None
    enabled: bool = True
    use_playwright: bool = False
    use_browser_profile: bool = False
    refresh_minutes: int = 180
    max_pages: int = 3
    request_delay_ms: int = 750
    notes: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    etag: str | None = None
    last_modified: str | None = None
    last_scan_at: datetime | None = None
    last_status: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(slots=True)
class NormalizedJob:
    id: int | None
    source_id: int
    source_name: str
    source_type: str
    external_id: str
    title: str
    company: str
    location: str
    remote_mode: str
    job_type: str | None
    clearance_terms: list[str]
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_interval: str | None
    salary_text: str | None
    posted_at: datetime | None
    url: str
    description: str
    summary_text: str
    skills: list[str]
    required_skills: list[str]
    preferred_skills: list[str]
    experience_years: float | None
    employment_text: str
    metadata: dict[str, Any]
    content_hash: str
    active: bool = True
    embedding: list[float] | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    last_updated_at: datetime | None = None


@dataclass(slots=True)
class FilterCriteria:
    location_query: str = ""
    remote_mode: str = "any"
    clearance_terms: list[str] = field(default_factory=list)
    job_type: str = "any"
    source_ids: list[int] = field(default_factory=list)


@dataclass(slots=True)
class DiscoveredSourceCandidate:
    name: str
    source_type: str
    url: str
    platform: str
    reason: str
    identifier: str | None = None
    use_playwright: bool = False
    use_browser_profile: bool = False


@dataclass(slots=True)
class MatchWeights:
    embedding: float = 0.68
    skill: float = 0.22
    experience: float = 0.10

    def normalized(self) -> "MatchWeights":
        total = self.embedding + self.skill + self.experience
        if total <= 0:
            return MatchWeights()
        return MatchWeights(
            embedding=self.embedding / total,
            skill=self.skill / total,
            experience=self.experience / total,
        )


@dataclass(slots=True)
class MatchResult:
    job_id: int
    score: float
    embedding_score: float
    skill_score: float
    experience_score: float
    matched_skills: list[str]
    missing_skills: list[str]
    reasons: list[str]
    job: NormalizedJob


@dataclass(slots=True)
class ScanResult:
    source: JobSourceConfig
    status: str
    jobs: list[NormalizedJob] = field(default_factory=list)
    response_etag: str | None = None
    response_last_modified: str | None = None
    error: str | None = None
    jobs_created: int = 0
    jobs_updated: int = 0
    jobs_unchanged: int = 0
    jobs_deactivated: int = 0
    pages_scanned: int = 0
    detail_pages_fetched: int = 0
    stopped_early: bool = False
    block_reason: str | None = None


@dataclass(slots=True)
class ScanSummary:
    started_at: datetime
    finished_at: datetime
    results: list[ScanResult]

    @property
    def total_jobs(self) -> int:
        return sum(len(result.jobs) for result in self.results)

    @property
    def total_created(self) -> int:
        return sum(result.jobs_created for result in self.results)

    @property
    def total_updated(self) -> int:
        return sum(result.jobs_updated for result in self.results)

    @property
    def total_unchanged(self) -> int:
        return sum(result.jobs_unchanged for result in self.results)

    @property
    def total_deactivated(self) -> int:
        return sum(result.jobs_deactivated for result in self.results)

    @property
    def error_count(self) -> int:
        return sum(1 for result in self.results if result.status in {"error", "blocked"})

    @property
    def blocked_count(self) -> int:
        return sum(1 for result in self.results if result.status == "blocked")

    @property
    def cancelled_count(self) -> int:
        return sum(1 for result in self.results if result.status == "cancelled")
