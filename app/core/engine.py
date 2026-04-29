from __future__ import annotations

import asyncio
import csv
import json
from dataclasses import replace
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.job_fetcher import JobFetcher
from app.core.matcher import JobMatcher
from app.core.normalizer import JobNormalizer
from app.core.resume_parser import ResumeParser
from app.core.types import FilterCriteria, JobSourceConfig, MatchResult, MatchWeights, ResumeProfile, ScanResult, ScanSummary
from app.db.storage import Storage
from app.utils.config import DEFAULT_SCAN_CONCURRENCY, DEFAULT_SETTINGS, EXPORTS_DIR, UPLOADS_DIR, ensure_directories
from app.utils.text import canonical_job_key, safe_filename, sanitize_source_url


class JobMatchEngine:
    def __init__(self, storage: Storage | None = None) -> None:
        ensure_directories()
        self.storage = storage or Storage()
        self.storage.init_db()
        self.resume_parser = ResumeParser()
        self.job_fetcher = JobFetcher(JobNormalizer())
        self._scan_lock = asyncio.Lock()
        self._scan_cancel_requested = asyncio.Event()
        self._matcher_cache: dict[tuple[str, float, float, float], JobMatcher] = {}

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

    def get_source(self, source_id: int) -> JobSourceConfig | None:
        return self.storage.get_source(source_id)

    def save_source(self, payload: JobSourceConfig) -> JobSourceConfig:
        payload.url = sanitize_source_url(payload.url, payload.source_type)
        return self.storage.upsert_source(payload)

    def delete_source(self, source_id: int) -> None:
        self.storage.delete_source(source_id)

    def get_settings(self) -> dict:
        return self.storage.get_settings()

    def update_settings(self, values: dict) -> None:
        self.storage.update_settings(values)

    def cancel_scan(self) -> bool:
        if not self._scan_lock.locked():
            return False
        self._scan_cancel_requested.set()
        return True

    def scan_running(self) -> bool:
        return self._scan_lock.locked()

    def is_manual_assist_source(self, source: JobSourceConfig) -> bool:
        source_type = self.job_fetcher.determine_source_type(source)
        return source.use_browser_profile or source_type == "indeed"

    def open_source_in_browser_profile(self, source_id: int) -> str:
        source = self.storage.get_source(source_id)
        if source is None:
            raise ValueError("Source not found.")
        browser_source = source if source.use_browser_profile else replace(source, use_browser_profile=True)
        return self.job_fetcher.open_source_in_browser_profile(browser_source)

    async def import_source_page(self, source_id: int) -> ScanResult:
        source = self.storage.get_source(source_id)
        if source is None:
            raise ValueError("Source not found.")
        known_jobs = self.storage.get_source_job_index(source.id or 0)
        browser_source = source if source.use_browser_profile else (
            replace(source, use_browser_profile=True)
            if self.job_fetcher.determine_source_type(source) == "indeed"
            else source
        )
        result = await self.job_fetcher.import_source_page(
            browser_source,
            known_jobs=known_jobs,
            max_jobs=int(self.get_settings().get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"])),
        )
        return self._store_manual_import(source, result, status="manual_import")

    async def import_saved_html(self, source_id: int, html_text: str) -> ScanResult:
        source = self.storage.get_source(source_id)
        if source is None:
            raise ValueError("Source not found.")
        result = self.job_fetcher.import_saved_html(
            source,
            html_text,
            max_jobs=int(self.get_settings().get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"])),
        )
        return self._store_manual_import(source, result, status="manual_import")

    async def import_job_urls(self, source_id: int, urls: list[str]) -> ScanResult:
        source = self.storage.get_source(source_id)
        if source is None:
            raise ValueError("Source not found.")
        if not urls:
            raise ValueError("Paste at least one job URL.")
        browser_source = source if source.use_browser_profile else (
            replace(source, use_browser_profile=True)
            if self.job_fetcher.determine_source_type(source) == "indeed"
            else source
        )
        result = await self.job_fetcher.import_job_urls(browser_source, urls)
        return self._store_manual_import(source, result, status="manual_import")

    async def scan_sources(
        self,
        source_ids: list[int] | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> ScanSummary:
        async with self._scan_lock:
            self._scan_cancel_requested.clear()
            sources = [source for source in self.storage.list_sources() if source.enabled]
            if source_ids:
                sources = [source for source in sources if source.id in source_ids]
            started_at = datetime.now(UTC)
            if not sources:
                return ScanSummary(started_at=started_at, finished_at=datetime.now(UTC), results=[])
            self._emit_progress(
                on_progress,
                event="scan_started",
                started_at=started_at,
                source_count=len(sources),
                sources=[
                    {"id": source.id, "name": source.name, "source_type": source.source_type}
                    for source in sources
                ],
            )

            settings = self.get_settings()
            max_jobs = int(settings.get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"]))
            semaphore = asyncio.Semaphore(DEFAULT_SCAN_CONCURRENCY)

            async def run_scan(source: JobSourceConfig):
                async with semaphore:
                    scan_id: int | None = None
                    if self._scan_cancel_requested.is_set():
                        result = self.job_fetcher.cancelled_result(source)
                    else:
                        self._emit_progress(
                            on_progress,
                            event="source_started",
                            source_id=source.id,
                            source_name=source.name,
                            source_type=source.source_type,
                        )
                        scan_id = self.storage.begin_scan(source.id)
                        known_jobs = self.storage.get_source_job_index(source.id or 0)
                        result = await self.job_fetcher.scan_source(
                            source,
                            max_jobs=max_jobs,
                            known_jobs=known_jobs,
                            progress_callback=on_progress,
                            cancel_requested=self._scan_cancel_requested.is_set,
                        )
                    if scan_id is not None:
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
                    self._emit_progress(
                        on_progress,
                        event="source_finished",
                        source_id=source.id,
                        source_name=source.name,
                        status=result.status,
                        jobs_found=len(result.jobs),
                        jobs_created=result.jobs_created,
                        jobs_updated=result.jobs_updated,
                        jobs_unchanged=result.jobs_unchanged,
                        jobs_deactivated=result.jobs_deactivated,
                        pages_scanned=result.pages_scanned,
                        detail_pages_fetched=result.detail_pages_fetched,
                        stopped_early=result.stopped_early,
                        block_reason=result.block_reason,
                        error=result.error,
                    )
                    return result

            results = await asyncio.gather(*(run_scan(source) for source in sources))
            summary = ScanSummary(started_at=started_at, finished_at=datetime.now(UTC), results=list(results))
            event_name = "scan_cancelled" if summary.cancelled_count else "scan_finished"
            self._emit_progress(
                on_progress,
                event=event_name,
                started_at=summary.started_at,
                finished_at=summary.finished_at,
                source_count=len(summary.results),
                total_jobs=summary.total_jobs,
                total_created=summary.total_created,
                total_updated=summary.total_updated,
                total_unchanged=summary.total_unchanged,
                total_deactivated=summary.total_deactivated,
                blocked_count=summary.blocked_count,
                cancelled_count=summary.cancelled_count,
                error_count=summary.error_count,
            )
            return summary

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
        matcher = self._get_matcher(str(settings.get("embedding_model_name", DEFAULT_SETTINGS["embedding_model_name"])), weights)
        jobs = self._deduplicate_jobs(jobs)
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
        sources = [
            source
            for source in self.storage.list_sources()
            if source.enabled and not self.is_manual_assist_source(source)
        ]
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

    def _store_manual_import(self, source: JobSourceConfig, result: ScanResult, *, status: str) -> ScanResult:
        if result.status != "manual_import":
            return result
        scan_id = self.storage.begin_scan(source.id)
        created, updated, unchanged = self.storage.merge_jobs(source, result.jobs)
        result.source = source
        result.jobs_created = created
        result.jobs_updated = updated
        result.jobs_unchanged = unchanged
        self.storage.update_source_scan_state(source.id or 0, status=status)
        self.storage.finish_scan(
            scan_id,
            status=status,
            jobs_found=len(result.jobs),
            jobs_created=created,
            jobs_updated=updated,
            jobs_unchanged=unchanged,
            jobs_deactivated=0,
        )
        return result

    def _get_matcher(self, model_name: str, weights: MatchWeights) -> JobMatcher:
        normalized = weights.normalized()
        cache_key = (
            model_name,
            round(normalized.embedding, 6),
            round(normalized.skill, 6),
            round(normalized.experience, 6),
        )
        matcher = self._matcher_cache.get(cache_key)
        if matcher is None:
            matcher = JobMatcher(model_name, normalized)
            self._matcher_cache[cache_key] = matcher
        return matcher

    @staticmethod
    def _emit_progress(on_progress: Callable[[dict], None] | None, **event: object) -> None:
        if on_progress is None:
            return
        try:
            on_progress(event)
        except Exception:
            return

    @staticmethod
    def _deduplicate_jobs(jobs: list) -> list:
        deduped: dict[str, object] = {}
        for job in jobs:
            key = canonical_job_key(
                job.title,
                job.company,
                job.location,
                job.metadata.get("canonical_url") or job.url,
                job.job_type,
            )
            current = deduped.get(key)
            if current is None or JobMatchEngine._job_sort_key(job) > JobMatchEngine._job_sort_key(current):
                deduped[key] = job
        return list(deduped.values())

    @staticmethod
    def _job_sort_key(job) -> tuple:
        recency = job.last_seen_at or job.last_updated_at or job.first_seen_at or datetime.min.replace(tzinfo=UTC)
        return (
            len(job.description or ""),
            len(job.required_skills or []),
            len(job.skills or []),
            recency,
        )

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
