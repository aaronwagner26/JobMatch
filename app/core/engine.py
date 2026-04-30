from __future__ import annotations

import asyncio
import csv
import json
import secrets
from dataclasses import replace
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

from app.core.job_fetcher import JobFetcher
from app.core.matcher import JobMatcher
from app.core.normalizer import JobNormalizer
from app.core.ollama_service import OllamaEnricher, OllamaStatus
from app.core.resume_parser import ResumeParser
from app.core.source_discovery import SourceDiscovery
from app.core.types import (
    DiscoveredSourceCandidate,
    FilterCriteria,
    JobSourceConfig,
    MatchResult,
    MatchWeights,
    NormalizedJob,
    ResumeProfile,
    ScanResult,
    ScanSummary,
)
from app.db.storage import Storage
from app.utils.config import DEFAULT_SCAN_CONCURRENCY, DEFAULT_SETTINGS, EXPORTS_DIR, UPLOADS_DIR, ensure_directories
from app.utils.text import capture_job_url, canonical_job_key, clean_job_text, normalize_whitespace, safe_filename, sanitize_source_url


class JobMatchEngine:
    def __init__(self, storage: Storage | None = None) -> None:
        ensure_directories()
        self.storage = storage or Storage()
        self.storage.init_db()
        self.resume_parser = ResumeParser()
        self.job_fetcher = JobFetcher(JobNormalizer())
        self.source_discovery = SourceDiscovery()
        self._scan_lock = asyncio.Lock()
        self._scan_cancel_requested = asyncio.Event()
        self._matcher_cache: dict[tuple[str, float, float, float], JobMatcher] = {}

    def save_resume(self, source_path: str | Path) -> ResumeProfile:
        path = Path(source_path)
        parsed = self.resume_parser.parse(path, llm_enricher=self._make_ollama_enricher())
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
        unsupported_reason = None
        if payload.source_type != "browser_capture":
            unsupported_reason = self.job_fetcher.unsupported_source_reason(payload)
        if unsupported_reason:
            raise ValueError(unsupported_reason)
        return self.storage.upsert_source(payload)

    def discover_sources(self, query: str) -> list[DiscoveredSourceCandidate]:
        return self.source_discovery.discover(query)

    def source_from_candidate(self, candidate: DiscoveredSourceCandidate) -> JobSourceConfig:
        return JobSourceConfig(
            id=None,
            name=candidate.name,
            source_type=candidate.source_type,
            url=candidate.url,
            identifier=candidate.identifier,
            enabled=True,
            use_playwright=candidate.use_playwright,
            use_browser_profile=candidate.use_browser_profile,
            refresh_minutes=180,
            max_pages=3,
            request_delay_ms=750,
            notes=f"Discovered via {candidate.platform}: {candidate.reason}",
        )

    def delete_source(self, source_id: int) -> None:
        self.storage.delete_source(source_id)

    def list_scanable_sources(self) -> list[JobSourceConfig]:
        return [
            source
            for source in self.storage.list_sources()
            if source.enabled and self.job_fetcher.determine_source_type(source) != "browser_capture"
        ]

    def get_settings(self) -> dict:
        return self.storage.get_settings()

    def update_settings(self, values: dict) -> None:
        self.storage.update_settings(values)

    def get_ollama_status(self) -> OllamaStatus:
        settings = self.get_settings()
        base_url = str(settings.get("ollama_base_url", ""))
        model_name = str(settings.get("ollama_model_name", ""))
        return OllamaEnricher(base_url=base_url, model_name=model_name).status()

    def get_browser_api_token(self) -> str:
        token = str(self.storage.get_setting("browser_api_token", "") or "")
        if token:
            return token
        token = secrets.token_urlsafe(24)
        self.storage.set_setting("browser_api_token", token)
        return token

    def rotate_browser_api_token(self) -> str:
        token = secrets.token_urlsafe(24)
        self.storage.set_setting("browser_api_token", token)
        return token

    def clear_scan_results(self) -> None:
        if self._scan_lock.locked():
            raise RuntimeError("Stop the active scan before clearing cached results.")
        self.storage.clear_scan_results()

    def cancel_scan(self) -> bool:
        if not self._scan_lock.locked():
            return False
        self._scan_cancel_requested.set()
        return True

    def scan_running(self) -> bool:
        return self._scan_lock.locked()

    def is_manual_assist_source(self, source: JobSourceConfig) -> bool:
        source_type = self.job_fetcher.determine_source_type(source)
        return source_type == "browser_capture" or source.use_browser_profile or source_type == "indeed"

    def import_browser_capture(self, payload: dict[str, object]) -> dict[str, object]:
        jobs_payload = payload.get("jobs")
        if not isinstance(jobs_payload, list) or not jobs_payload:
            raise ValueError("Browser capture did not include any jobs.")

        page = payload.get("page") if isinstance(payload.get("page"), dict) else {}
        source_meta = payload.get("source") if isinstance(payload.get("source"), dict) else {}
        page_url = sanitize_source_url(
            str(
                (page or {}).get("url")
                or (source_meta or {}).get("url")
                or (payload.get("page_url") if isinstance(payload.get("page_url"), str) else "")
            ),
            "browser_capture",
        )
        if not page_url:
            raise ValueError("Browser capture is missing the page URL.")

        source = self._resolve_browser_capture_source(page_url, page or {}, source_meta or {}, jobs_payload)
        llm_enricher = self._make_ollama_enricher()
        normalized_jobs = []
        for item in jobs_payload:
            if not isinstance(item, dict):
                continue
            prepared = self._prepare_browser_capture_payload(source, item, page or {}, page_url, payload)
            try:
                normalized_jobs.append(self.job_fetcher.normalizer.normalize(source, prepared, llm_enricher=llm_enricher))
            except Exception:
                continue

        if not normalized_jobs:
            raise ValueError("No usable jobs were found in the browser capture.")

        scan_id = self.storage.begin_scan(source.id)
        created, updated, unchanged = self.storage.merge_jobs(source, normalized_jobs)
        self.storage.update_source_scan_state(source.id or 0, status="browser_capture")
        self.storage.finish_scan(
            scan_id,
            status="browser_capture",
            jobs_found=len(normalized_jobs),
            jobs_created=created,
            jobs_updated=updated,
            jobs_unchanged=unchanged,
            jobs_deactivated=0,
        )
        return {
            "source_id": source.id,
            "source_name": source.name,
            "source_url": source.url,
            "jobs_imported": len(normalized_jobs),
            "jobs_created": created,
            "jobs_updated": updated,
            "jobs_unchanged": unchanged,
        }

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
        llm_enricher = self._make_ollama_enricher()
        result = await self.job_fetcher.import_source_page(
            browser_source,
            known_jobs=known_jobs,
            max_jobs=int(self.get_settings().get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"])),
            llm_enricher=llm_enricher,
        )
        return self._store_manual_import(source, result, status="manual_import")

    async def import_saved_html(self, source_id: int, html_text: str) -> ScanResult:
        source = self.storage.get_source(source_id)
        if source is None:
            raise ValueError("Source not found.")
        llm_enricher = self._make_ollama_enricher()
        result = self.job_fetcher.import_saved_html(
            source,
            html_text,
            max_jobs=int(self.get_settings().get("max_source_jobs", DEFAULT_SETTINGS["max_source_jobs"])),
            llm_enricher=llm_enricher,
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
        llm_enricher = self._make_ollama_enricher()
        result = await self.job_fetcher.import_job_urls(browser_source, urls, llm_enricher=llm_enricher)
        return self._store_manual_import(source, result, status="manual_import")

    async def scan_sources(
        self,
        source_ids: list[int] | None = None,
        on_progress: Callable[[dict], None] | None = None,
    ) -> ScanSummary:
        async with self._scan_lock:
            self._scan_cancel_requested.clear()
            sources = self.list_scanable_sources()
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
            llm_enricher = self._make_ollama_enricher()

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
                            llm_enricher=llm_enricher,
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

    def list_filtered_jobs(
        self,
        filters: FilterCriteria | None = None,
        *,
        dedupe: bool = False,
    ) -> list[NormalizedJob]:
        filters = filters or FilterCriteria()
        jobs = self.storage.list_jobs(active_only=True, source_ids=filters.source_ids or None)
        if dedupe:
            jobs = self._deduplicate_jobs(jobs)
        return [job for job in jobs if JobMatcher._job_matches_filters(job, filters)]

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
                        "salary_text",
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
                            "salary_text": match.job.salary_text or "",
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

    def _resolve_browser_capture_source(
        self,
        page_url: str,
        page: dict[str, object],
        source_meta: dict[str, object],
        jobs_payload: list[object],
    ) -> JobSourceConfig:
        source_id = source_meta.get("id")
        if isinstance(source_id, int):
            existing = self.storage.get_source(source_id)
            if existing is not None:
                return existing

        existing = self.storage.find_source_by_url(page_url, source_type="browser_capture")
        if existing is not None:
            return existing

        company = normalize_whitespace(
            str(source_meta.get("company") or self._first_company_from_jobs(jobs_payload) or "")
        )
        site_name = normalize_whitespace(str(source_meta.get("site") or page.get("site") or self._host_label(page_url)))
        page_title = normalize_whitespace(str(page.get("title") or ""))
        source_name = normalize_whitespace(
            str(source_meta.get("name") or self._browser_capture_source_name(company, site_name, page_title))
        )
        notes = normalize_whitespace(
            f"Managed by browser capture from {site_name}. Refresh this source from the extension instead of Scan now."
        )
        return self.storage.upsert_source(
            JobSourceConfig(
                id=None,
                name=source_name,
                source_type="browser_capture",
                url=page_url,
                enabled=True,
                refresh_minutes=180,
                max_pages=1,
                request_delay_ms=0,
                notes=notes,
            )
        )

    def _prepare_browser_capture_payload(
        self,
        source: JobSourceConfig,
        item: dict[str, object],
        page: dict[str, object],
        page_url: str,
        root_payload: dict[str, object],
    ) -> dict[str, object]:
        payload = dict(item)
        raw_id = normalize_whitespace(str(item.get("raw_id") or item.get("external_id") or ""))
        payload["url"] = capture_job_url(
            str(item.get("url") or ""),
            page_url=page_url,
            raw_id=raw_id,
        ) or capture_job_url(page_url)
        payload["company"] = normalize_whitespace(str(item.get("company") or source.name))
        payload["title"] = normalize_whitespace(str(item.get("title") or ""))
        payload["location"] = normalize_whitespace(str(item.get("location") or ""))
        payload["summary"] = clean_job_text(str(item.get("summary") or ""))
        payload["description"] = clean_job_text(str(item.get("description") or payload["summary"] or ""))
        payload["employment_text"] = normalize_whitespace(str(item.get("employment_text") or ""))
        metadata = dict(item.get("metadata") or {}) if isinstance(item.get("metadata"), dict) else {}
        metadata.update(
            {
                "capture_mode": "browser_extension",
                "captured_page_url": page_url,
                "captured_page_title": normalize_whitespace(str(page.get("title") or "")),
                "captured_site": normalize_whitespace(str(page.get("site") or "")),
                "capture_parser": normalize_whitespace(str(root_payload.get("parser") or page.get("parser") or "")),
            }
        )
        payload["metadata"] = metadata
        payload["canonical_url"] = payload["url"]
        if not raw_id:
            raw_id = normalize_whitespace(str(payload["url"]))
        payload["raw_id"] = raw_id
        return payload

    @staticmethod
    def _browser_capture_source_name(company: str, site_name: str, page_title: str) -> str:
        label = company or page_title or site_name or "Captured Jobs"
        if site_name and company:
            return f"Capture: {company} ({site_name})"
        return f"Capture: {label}"

    @staticmethod
    def _first_company_from_jobs(jobs_payload: list[object]) -> str:
        for item in jobs_payload:
            if not isinstance(item, dict):
                continue
            company = normalize_whitespace(str(item.get("company") or ""))
            if company:
                return company
        return ""

    @staticmethod
    def _host_label(url: str) -> str:
        host = urlsplit(url).netloc.casefold()
        labels = [label for label in host.split(".") if label and label not in {"www", "jobs", "careers"}]
        if not labels:
            return host or "Captured Jobs"
        return labels[0].replace("-", " ").title()

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

    def _make_ollama_enricher(self) -> OllamaEnricher | None:
        settings = self.get_settings()
        if not bool(settings.get("ollama_enabled")):
            return None
        base_url = normalize_whitespace(str(settings.get("ollama_base_url") or ""))
        model_name = normalize_whitespace(str(settings.get("ollama_model_name") or ""))
        if not base_url or not model_name:
            return None
        return OllamaEnricher(
            base_url=base_url,
            model_name=model_name,
            resume_enabled=bool(settings.get("ollama_enhance_resume", True)),
            job_enabled=bool(settings.get("ollama_enhance_jobs", True)),
            max_job_enrichments=int(settings.get("ollama_max_job_enrichments", 20) or 0),
        )

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
                "salary_min": match.job.salary_min,
                "salary_max": match.job.salary_max,
                "salary_currency": match.job.salary_currency,
                "salary_interval": match.job.salary_interval,
                "salary_text": match.job.salary_text,
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
