from __future__ import annotations

import asyncio
import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.job_fetcher import JobFetcher
from app.core.matcher import JobMatcher
from app.core.normalizer import JobNormalizer
from app.core.resume_parser import ResumeParser
from app.core.types import FilterCriteria, JobSourceConfig, MatchResult, MatchWeights, ResumeProfile, ScanSummary
from app.db.storage import Storage
from app.utils.config import DEFAULT_SCAN_CONCURRENCY, DEFAULT_SETTINGS, EXPORTS_DIR, UPLOADS_DIR, ensure_directories
from app.utils.text import safe_filename


class JobMatchEngine:
    def __init__(self, storage: Storage | None = None) -> None:
        ensure_directories()
        self.storage = storage or Storage()
        self.storage.init_db()
        self.resume_parser = ResumeParser()
        self.job_fetcher = JobFetcher(JobNormalizer())
        self._scan_lock = asyncio.Lock()

    def save_resume(self, source_path: str | Path) -> ResumeProfile:
        path = Path(source_path)
        parsed = self.resume_parser.parse(path)
        stored_name = f"{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}-{safe_filename(path.stem, path.suffix)}"
        stored_path = UPLOADS_DIR / stored_name
        stored_path.write_bytes(path.read_bytes())
        parsed.file_path = str(stored_path)
        return self.storage.save_resume(parsed)

    def get_active_resume(self) -> ResumeProfile | None:
        return self.storage.get_active_resume()

    def list_sources(self) -> list[JobSourceConfig]:
        return self.storage.list_sources()

    def save_source(self, payload: JobSourceConfig) -> JobSourceConfig:
        return self.storage.upsert_source(payload)

    def delete_source(self, source_id: int) -> None:
        self.storage.delete_source(source_id)

    def get_settings(self) -> dict:
        return self.storage.get_settings()

    def update_settings(self, values: dict) -> None:
        self.storage.update_settings(values)

    async def scan_sources(self, source_ids: list[int] | None = None) -> ScanSummary:
        async with self._scan_lock:
            sources = [source for source in self.storage.list_sources() if source.enabled]
            if source_ids:
                sources = [source for source in sources if source.id in source_ids]
            started_at = datetime.now(UTC)
            if not sources:
                return ScanSummary(started_at=started_at, finished_at=datetime.now(UTC), results=[])

            settings = self.get_settings()
            max_jobs = int(settings.get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"]))
            semaphore = asyncio.Semaphore(DEFAULT_SCAN_CONCURRENCY)

            async def run_scan(source: JobSourceConfig):
                async with semaphore:
                    scan_id = self.storage.begin_scan(source.id)
                    result = await self.job_fetcher.scan_source(source, max_jobs=max_jobs)
                    if result.status == "ok":
                        created, updated, unchanged, deactivated = self.storage.upsert_jobs(source, result.jobs)
                        result.jobs_created = created
                        result.jobs_updated = updated
                        result.jobs_unchanged = unchanged
                        result.jobs_deactivated = deactivated
                        self.storage.update_source_scan_state(
                            source.id or 0,
                            status=result.status,
                            etag=result.response_etag,
                            last_modified=result.response_last_modified,
                        )
                        self.storage.finish_scan(
                            scan_id,
                            status=result.status,
                            jobs_found=len(result.jobs),
                            jobs_created=created,
                            jobs_updated=updated,
                            jobs_unchanged=unchanged,
                            jobs_deactivated=deactivated,
                        )
                    elif result.status == "not_modified":
                        self.storage.update_source_scan_state(
                            source.id or 0,
                            status=result.status,
                            etag=result.response_etag,
                            last_modified=result.response_last_modified,
                        )
                        self.storage.finish_scan(scan_id, status=result.status)
                    else:
                        self.storage.update_source_scan_state(source.id or 0, status=result.status)
                        self.storage.finish_scan(scan_id, status=result.status, error_text=result.error)
                    return result

            results = await asyncio.gather(*(run_scan(source) for source in sources))
            return ScanSummary(started_at=started_at, finished_at=datetime.now(UTC), results=list(results))

    def get_ranked_matches(self, filters: FilterCriteria | None = None) -> list[MatchResult]:
        filters = filters or FilterCriteria()
        resume = self.storage.get_active_resume()
        if resume is None:
            raise ValueError("Upload a resume before running matches.")
        jobs = self.storage.list_jobs(active_only=True, source_ids=filters.source_ids or None)
        settings = self.get_settings()
        weights = MatchWeights(
            embedding=float(settings.get("embedding_weight", DEFAULT_SETTINGS["embedding_weight"])),
            skill=float(settings.get("skill_weight", DEFAULT_SETTINGS["skill_weight"])),
            experience=float(settings.get("experience_weight", DEFAULT_SETTINGS["experience_weight"])),
        )
        matcher = JobMatcher(str(settings.get("embedding_model_name", DEFAULT_SETTINGS["embedding_model_name"])), weights)
        resume, jobs, job_embeddings = matcher.ensure_embeddings(resume, jobs)
        if resume.id and resume.embedding:
            self.storage.save_resume_embedding(resume.id, resume.embedding)
        self.storage.save_job_embeddings({job_id: embedding for job_id, embedding in job_embeddings.items() if embedding})
        return matcher.match(resume, jobs, filters)

    def export_matches(self, export_format: str, matches: list[MatchResult]) -> Path:
        timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        export_path = EXPORTS_DIR / f"jobmatch-results-{timestamp}.{export_format}"
        if export_format == "json":
            payload = [self._match_to_dict(match) for match in matches]
            export_path.write_text(json.dumps(payload, indent=2, ensure_ascii=True), encoding="utf-8")
            return export_path
        if export_format == "csv":
            with export_path.open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(
                    handle,
                    fieldnames=[
                        "score",
                        "embedding_score",
                        "skill_score",
                        "experience_score",
                        "title",
                        "company",
                        "location",
                        "remote_mode",
                        "job_type",
                        "clearance_terms",
                        "matched_skills",
                        "missing_skills",
                        "url",
                    ],
                )
                writer.writeheader()
                for match in matches:
                    writer.writerow(
                        {
                            "score": round(match.score, 4),
                            "embedding_score": round(match.embedding_score, 4),
                            "skill_score": round(match.skill_score, 4),
                            "experience_score": round(match.experience_score, 4),
                            "title": match.job.title,
                            "company": match.job.company,
                            "location": match.job.location,
                            "remote_mode": match.job.remote_mode,
                            "job_type": match.job.job_type or "",
                            "clearance_terms": ", ".join(match.job.clearance_terms),
                            "matched_skills": ", ".join(match.matched_skills),
                            "missing_skills": ", ".join(match.missing_skills),
                            "url": match.job.url,
                        }
                    )
            return export_path
        raise ValueError("Unsupported export format.")

    def should_run_scheduled_scan(self) -> bool:
        settings = self.get_settings()
        if not settings.get("scheduler_enabled"):
            return False
        interval = int(settings.get("scheduler_interval_minutes", DEFAULT_SETTINGS["scheduler_interval_minutes"]))
        sources = [source for source in self.storage.list_sources() if source.enabled]
        if not sources:
            return False
        now = datetime.now(UTC)
        for source in sources:
            if source.last_scan_at is None:
                return True
            if now - source.last_scan_at >= timedelta(minutes=interval):
                return True
        return False

    def list_recent_scans(self, limit: int = 25) -> list[dict]:
        return self.storage.list_scans(limit=limit)

    @staticmethod
    def _match_to_dict(match: MatchResult) -> dict:
        return {
            "score": match.score,
            "embedding_score": match.embedding_score,
            "skill_score": match.skill_score,
            "experience_score": match.experience_score,
            "matched_skills": match.matched_skills,
            "missing_skills": match.missing_skills,
            "reasons": match.reasons,
            "job": {
                "id": match.job.id,
                "source_id": match.job.source_id,
                "source_name": match.job.source_name,
                "source_type": match.job.source_type,
                "title": match.job.title,
                "company": match.job.company,
                "location": match.job.location,
                "remote_mode": match.job.remote_mode,
                "job_type": match.job.job_type,
                "clearance_terms": match.job.clearance_terms,
                "url": match.job.url,
                "posted_at": match.job.posted_at.isoformat() if match.job.posted_at else None,
                "summary_text": match.job.summary_text,
                "skills": match.job.skills,
                "required_skills": match.job.required_skills,
                "preferred_skills": match.job.preferred_skills,
                "experience_years": match.job.experience_years,
            },
        }

