from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig, NormalizedJob, ScanResult
from app.utils.config import (
    BROWSER_PROFILES_DIR,
    DEFAULT_BROWSER_CHALLENGE_WAIT_SECONDS,
    DEFAULT_DETAIL_FETCH_CONCURRENCY,
    DEFAULT_DETAIL_FETCH_LIMIT,
    DEFAULT_EARLY_STOP_CONSECUTIVE_PAGES,
    DEFAULT_EARLY_STOP_KNOWN_RATIO,
    DEFAULT_EARLY_STOP_MIN_PAGES,
    DEFAULT_HEADERS,
    DEFAULT_HTTP_TIMEOUT,
    DEFAULT_REQUEST_BACKOFF_MULTIPLIER,
    DEFAULT_REQUEST_MAX_RETRIES,
)
from app.utils.text import absolute_url, normalize_whitespace, safe_filename, sanitize_source_url, strip_html

logger = logging.getLogger(__name__)

JSON_LD_JOB_POSTING = "jobposting"
RETRYABLE_STATUS_CODES = {408, 429, 500, 502, 503, 504}
SECURITY_CHECK_MARKERS = (
    "additional verification required",
    "attention required",
    "cloudflare",
    "enable javascript and cookies to continue",
    "just a moment",
    "ray id",
    "security check - indeed.com",
    "troubleshooting cloudflare errors",
)


class SourceBlockedError(RuntimeError):
    def __init__(self, message: str, *, reason: str = "security_check") -> None:
        super().__init__(message)
        self.reason = reason


class ScanCancelledError(RuntimeError):
    pass


class SourceThrottle:
    def __init__(self, delay_ms: int) -> None:
        self.delay_seconds = max(delay_ms, 0) / 1000
        self._lock = asyncio.Lock()
        self._next_allowed_at = 0.0

    async def wait(self) -> None:
        if self.delay_seconds <= 0:
            return
        loop = asyncio.get_running_loop()
        async with self._lock:
            now = loop.time()
            sleep_for = max(0.0, self._next_allowed_at - now)
            if sleep_for > 0:
                await asyncio.sleep(sleep_for)
            self._next_allowed_at = loop.time() + self.delay_seconds


class JobFetcher:
    def __init__(self, normalizer: JobNormalizer) -> None:
        self.normalizer = normalizer

    async def scan_source(
        self,
        source: JobSourceConfig,
        max_jobs: int = 120,
        known_jobs: dict[str, dict[str, Any]] | None = None,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> ScanResult:
        source_type = self._determine_source_type(source)
        source.source_type = source_type
        source.url = sanitize_source_url(source.url, source_type)
        throttle = SourceThrottle(source.request_delay_ms)
        diagnostics = {"pages_scanned": 0, "detail_pages_fetched": 0, "stopped_early": False}
        try:
            self._raise_if_cancelled(cancel_requested)
            raw_jobs, etag, last_modified, not_modified = await self._fetch_jobs(
                source,
                source_type,
                max_jobs=max_jobs,
                known_jobs=known_jobs or {},
                throttle=throttle,
                progress_callback=progress_callback,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
            if not_modified:
                return ScanResult(
                    source=source,
                    status="not_modified",
                    response_etag=etag,
                    response_last_modified=last_modified,
                    pages_scanned=int(diagnostics["pages_scanned"]),
                    detail_pages_fetched=int(diagnostics["detail_pages_fetched"]),
                    stopped_early=bool(diagnostics["stopped_early"]),
                )

            normalized_jobs: list[NormalizedJob] = []
            for payload in raw_jobs[:max_jobs]:
                try:
                    normalized_jobs.append(self.normalizer.normalize(source, payload))
                except Exception as exc:
                    logger.warning("Skipping malformed job from %s: %s", source.name, exc)
            return ScanResult(
                source=source,
                status="ok",
                jobs=normalized_jobs,
                response_etag=etag,
                response_last_modified=last_modified,
                pages_scanned=int(diagnostics["pages_scanned"]),
                detail_pages_fetched=int(diagnostics["detail_pages_fetched"]),
                stopped_early=bool(diagnostics["stopped_early"]),
            )
        except ScanCancelledError:
            logger.info("Source scan cancelled for %s", source.name)
            return self.cancelled_result(
                source,
                pages_scanned=int(diagnostics["pages_scanned"]),
                detail_pages_fetched=int(diagnostics["detail_pages_fetched"]),
                stopped_early=bool(diagnostics["stopped_early"]),
            )
        except SourceBlockedError as exc:
            logger.info("Source scan blocked for %s: %s", source.name, exc)
            return ScanResult(
                source=source,
                status="blocked",
                error=str(exc),
                pages_scanned=int(diagnostics["pages_scanned"]),
                detail_pages_fetched=int(diagnostics["detail_pages_fetched"]),
                stopped_early=bool(diagnostics["stopped_early"]),
                block_reason=exc.reason,
            )
        except Exception as exc:
            logger.exception("Source scan failed for %s", source.name)
            return ScanResult(
                source=source,
                status="error",
                error=str(exc),
                pages_scanned=int(diagnostics["pages_scanned"]),
                detail_pages_fetched=int(diagnostics["detail_pages_fetched"]),
                stopped_early=bool(diagnostics["stopped_early"]),
            )

    @staticmethod
    def cancelled_result(
        source: JobSourceConfig,
        *,
        pages_scanned: int = 0,
        detail_pages_fetched: int = 0,
        stopped_early: bool = False,
    ) -> ScanResult:
        return ScanResult(
            source=source,
            status="cancelled",
            error="Cancelled by user.",
            pages_scanned=pages_scanned,
            detail_pages_fetched=detail_pages_fetched,
            stopped_early=stopped_early,
        )

    async def _fetch_jobs(
        self,
        source: JobSourceConfig,
        source_type: str,
        *,
        max_jobs: int,
        known_jobs: dict[str, dict[str, Any]],
        throttle: SourceThrottle,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        diagnostics: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        if source_type == "greenhouse":
            return await self._fetch_greenhouse(
                source,
                throttle,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
        if source_type == "lever":
            return await self._fetch_lever(
                source,
                throttle,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
        if source_type == "indeed":
            return await self._fetch_search_page(
                source,
                parser="indeed",
                max_jobs=max_jobs,
                known_jobs=known_jobs,
                throttle=throttle,
                progress_callback=progress_callback,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
        if source_type == "clearance":
            return await self._fetch_search_page(
                source,
                parser="clearance",
                max_jobs=max_jobs,
                known_jobs=known_jobs,
                throttle=throttle,
                progress_callback=progress_callback,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
        return await self._fetch_search_page(
            source,
            parser="generic",
            max_jobs=max_jobs,
            known_jobs=known_jobs,
            throttle=throttle,
            progress_callback=progress_callback,
            diagnostics=diagnostics,
            cancel_requested=cancel_requested,
        )

    async def _fetch_greenhouse(
        self,
        source: JobSourceConfig,
        throttle: SourceThrottle,
        *,
        diagnostics: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        self._raise_if_cancelled(cancel_requested)
        diagnostics["pages_scanned"] = 1
        identifier = source.identifier or self._extract_greenhouse_identifier(source.url)
        if not identifier:
            raise ValueError("Greenhouse source requires a board token or board URL.")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true"
        payload, response = await self._request_json(
            source,
            endpoint,
            throttle=throttle,
            cancel_requested=cancel_requested,
        )
        jobs = []
        for item in payload.get("jobs", []):
            jobs.append(
                {
                    "raw_id": item.get("id"),
                    "title": item.get("title"),
                    "company": source.name,
                    "location": ((item.get("location") or {}).get("name") if isinstance(item.get("location"), dict) else item.get("location")),
                    "description": strip_html(item.get("content")),
                    "url": item.get("absolute_url"),
                    "posted_at": item.get("updated_at"),
                    "metadata": {"departments": item.get("departments"), "offices": item.get("offices")},
                }
            )
        return jobs, response.headers.get("etag"), response.headers.get("last-modified"), response.status_code == 304

    async def _fetch_lever(
        self,
        source: JobSourceConfig,
        throttle: SourceThrottle,
        *,
        diagnostics: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        self._raise_if_cancelled(cancel_requested)
        diagnostics["pages_scanned"] = 1
        identifier = source.identifier or self._extract_lever_identifier(source.url)
        if not identifier:
            raise ValueError("Lever source requires a company slug or postings URL.")
        endpoint = f"https://api.lever.co/v0/postings/{identifier}?mode=json"
        payload, response = await self._request_json(
            source,
            endpoint,
            throttle=throttle,
            cancel_requested=cancel_requested,
        )
        jobs = []
        for item in payload:
            categories = item.get("categories") or {}
            requirements = ""
            if item.get("lists"):
                text_blocks = [strip_html(section.get("text")) for section in item["lists"] if isinstance(section, dict)]
                requirements = "\n".join(filter(None, text_blocks))
            jobs.append(
                {
                    "raw_id": item.get("id"),
                    "title": item.get("text"),
                    "company": source.name,
                    "location": categories.get("location"),
                    "description": strip_html(item.get("descriptionPlain") or item.get("description")),
                    "requirements_text": requirements,
                    "url": item.get("hostedUrl"),
                    "posted_at": item.get("createdAt"),
                    "job_type": categories.get("commitment"),
                    "metadata": {"team": categories.get("team"), "categories": categories},
                }
            )
        return jobs, response.headers.get("etag"), response.headers.get("last-modified"), response.status_code == 304

    async def _fetch_search_page(
        self,
        source: JobSourceConfig,
        *,
        parser: str,
        max_jobs: int,
        known_jobs: dict[str, dict[str, Any]],
        throttle: SourceThrottle,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        diagnostics: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        if self._should_use_browser_session(source, parser):
            return await self._fetch_search_page_via_browser_session(
                source,
                parser=parser,
                max_jobs=max_jobs,
                known_jobs=known_jobs,
                throttle=throttle,
                progress_callback=progress_callback,
                diagnostics=diagnostics,
                cancel_requested=cancel_requested,
            )
        url = sanitize_source_url(source.url, source.source_type)
        first_response: httpx.Response | None = None
        all_jobs: list[dict[str, Any]] = []
        seen_external_ids: set[str] = set()
        seen_page_urls: set[str] = {url}
        remaining_detail_budget = self._detail_fetch_budget(source, parser)
        consecutive_known_pages = 0
        page_number = 0
        max_pages = max(1, source.max_pages)

        while url and page_number < max_pages and len(all_jobs) < max_jobs:
            self._raise_if_cancelled(cancel_requested)
            page_number += 1
            diagnostics["pages_scanned"] = page_number
            page_jobs: list[dict[str, Any]] = []
            next_url: str | None = None
            used_dynamic_fallback = False
            try:
                response = await self._request_text(
                    source,
                    url,
                    throttle=throttle,
                    cancel_requested=cancel_requested,
                )
                if page_number == 1 and response.status_code == 304:
                    return [], response.headers.get("etag"), response.headers.get("last-modified"), True
                first_response = first_response or response

                page_html = response.text
                if self._looks_like_security_check(page_html):
                    raise SourceBlockedError(
                        self._security_check_message(source, parser, needs_browser_profile=not source.use_browser_profile)
                    )
                page_jobs, next_url = self._parse_html_jobs(page_html, url, parser=parser)
            except httpx.HTTPStatusError as exc:
                status_code = exc.response.status_code if exc.response is not None else None
                if not self._should_try_dynamic_fallback(parser, status_code):
                    raise
                used_dynamic_fallback = True
                reason = f"HTTP {status_code}" if status_code is not None else "request failure"
                logger.info("Falling back to Playwright for %s page %s after %s", source.name, page_number, reason)
                self._emit_progress(
                    progress_callback,
                    event="source_fallback",
                    source_id=source.id,
                    source_name=source.name,
                    page=page_number,
                    reason=reason,
                )
                dynamic_html = await self._fetch_dynamic_html(
                    source,
                    url,
                    progress_callback=progress_callback,
                    cancel_requested=cancel_requested,
                )
                if not dynamic_html:
                    raise RuntimeError(
                        f"{source.name} was blocked by {reason}, and browser fallback could not recover the page."
                    ) from exc
                if self._looks_like_security_check(dynamic_html):
                    raise SourceBlockedError(
                        self._security_check_message(source, parser, needs_browser_profile=not source.use_browser_profile)
                    ) from exc
                page_jobs, next_url = self._parse_html_jobs(dynamic_html, url, parser=parser)
                if not page_jobs:
                    raise RuntimeError(
                        f"{source.name} was blocked by {reason}. Browser fallback loaded the page, but no jobs were detected."
                    ) from exc

            if (source.use_playwright or not page_jobs) and not used_dynamic_fallback and parser in {"indeed", "clearance", "generic"}:
                dynamic_html = await self._fetch_dynamic_html(
                    source,
                    source.url if page_number == 1 else url,
                    progress_callback=progress_callback,
                    cancel_requested=cancel_requested,
                )
                if dynamic_html:
                    if self._looks_like_security_check(dynamic_html):
                        raise SourceBlockedError(
                            self._security_check_message(source, parser, needs_browser_profile=not source.use_browser_profile)
                        )
                    dynamic_jobs, dynamic_next_url = self._parse_html_jobs(dynamic_html, url, parser=parser)
                    if dynamic_jobs:
                        page_jobs = dynamic_jobs
                    if dynamic_next_url:
                        next_url = dynamic_next_url

            prepared_jobs: list[dict[str, Any]] = []
            known_count = 0
            for payload in page_jobs:
                prepared = self._prepare_payload(source, payload, known_jobs)
                external_id = str(prepared["external_id"])
                if external_id in seen_external_ids:
                    continue
                seen_external_ids.add(external_id)
                if prepared.get("_known_listing"):
                    known_count += 1
                prepared_jobs.append(prepared)
                if len(all_jobs) + len(prepared_jobs) >= max_jobs:
                    break

            if not prepared_jobs:
                break

            jobs_requiring_detail = [job for job in prepared_jobs if job.get("_requires_detail")]
            if remaining_detail_budget > 0 and jobs_requiring_detail:
                self._raise_if_cancelled(cancel_requested)
                detail_batch = jobs_requiring_detail[:remaining_detail_budget]
                diagnostics["detail_pages_fetched"] = int(diagnostics["detail_pages_fetched"]) + len(detail_batch)
                self._emit_progress(
                    progress_callback,
                    event="source_detail",
                    source_id=source.id,
                    source_name=source.name,
                    detail_pages=len(detail_batch),
                    page=page_number,
                )
                await self._enrich_detail_pages(
                    detail_batch,
                    source,
                    throttle=throttle,
                    cancel_requested=cancel_requested,
                )
                remaining_detail_budget = max(0, remaining_detail_budget - len(detail_batch))

            all_jobs.extend(prepared_jobs)
            new_or_changed_count = len(prepared_jobs) - known_count
            self._emit_progress(
                progress_callback,
                event="source_page",
                source_id=source.id,
                source_name=source.name,
                page=page_number,
                jobs_kept=len(prepared_jobs),
                known_jobs=known_count,
                new_or_changed_jobs=new_or_changed_count,
                total_jobs=len(all_jobs),
            )

            if self._is_mostly_known_page(prepared_jobs, known_count, new_or_changed_count):
                consecutive_known_pages += 1
            else:
                consecutive_known_pages = 0

            if (
                page_number >= DEFAULT_EARLY_STOP_MIN_PAGES
                and consecutive_known_pages >= DEFAULT_EARLY_STOP_CONSECUTIVE_PAGES
            ):
                diagnostics["stopped_early"] = True
                logger.info(
                    "Stopping early for %s after %s pages because recent pages were mostly known jobs.",
                    source.name,
                    page_number,
                )
                self._emit_progress(
                    progress_callback,
                    event="source_early_stop",
                    source_id=source.id,
                    source_name=source.name,
                    page=page_number,
                )
                break

            if not next_url or next_url in seen_page_urls:
                break
            next_url = sanitize_source_url(next_url, source.source_type)
            seen_page_urls.add(next_url)
            url = next_url

        if first_response is None:
            return all_jobs[:max_jobs], None, None, False
        return (
            all_jobs[:max_jobs],
            first_response.headers.get("etag"),
            first_response.headers.get("last-modified"),
            False,
        )

    async def _fetch_search_page_via_browser_session(
        self,
        source: JobSourceConfig,
        *,
        parser: str,
        max_jobs: int,
        known_jobs: dict[str, dict[str, Any]],
        throttle: SourceThrottle,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        diagnostics: dict[str, Any],
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        try:
            from playwright.async_api import async_playwright
        except Exception as exc:
            raise RuntimeError("Playwright is required for persistent browser-profile scans.") from exc

        url = sanitize_source_url(source.url, source.source_type)
        all_jobs: list[dict[str, Any]] = []
        seen_external_ids: set[str] = set()
        seen_page_urls: set[str] = {url}
        remaining_detail_budget = self._detail_fetch_budget(source, parser)
        consecutive_known_pages = 0
        page_number = 0
        max_pages = max(1, source.max_pages)

        self._emit_progress(
            progress_callback,
            event="source_browser_session",
            source_id=source.id,
            source_name=source.name,
        )

        async with async_playwright() as playwright:
            context, page = await self._open_browser_context(playwright, source)
            try:
                while url and page_number < max_pages and len(all_jobs) < max_jobs:
                    self._raise_if_cancelled(cancel_requested)
                    page_number += 1
                    diagnostics["pages_scanned"] = page_number
                    await throttle.wait()
                    html = await self._navigate_and_capture_browser_html(
                        page,
                        source,
                        url,
                        progress_callback=progress_callback,
                        cancel_requested=cancel_requested,
                    )
                    if not html:
                        raise RuntimeError(f"{source.name} browser session could not recover {url}.")
                    if self._looks_like_security_check(html):
                        raise SourceBlockedError(
                            self._security_check_message(source, parser, needs_browser_profile=False)
                        )

                    page_jobs, next_url = self._parse_html_jobs(html, url, parser=parser)
                    prepared_jobs: list[dict[str, Any]] = []
                    known_count = 0
                    for payload in page_jobs:
                        prepared = self._prepare_payload(source, payload, known_jobs)
                        external_id = str(prepared["external_id"])
                        if external_id in seen_external_ids:
                            continue
                        seen_external_ids.add(external_id)
                        if prepared.get("_known_listing"):
                            known_count += 1
                        prepared_jobs.append(prepared)
                        if len(all_jobs) + len(prepared_jobs) >= max_jobs:
                            break

                    if not prepared_jobs:
                        break

                    jobs_requiring_detail = [job for job in prepared_jobs if job.get("_requires_detail")]
                    if remaining_detail_budget > 0 and jobs_requiring_detail:
                        self._raise_if_cancelled(cancel_requested)
                        detail_batch = jobs_requiring_detail[:remaining_detail_budget]
                        diagnostics["detail_pages_fetched"] = int(diagnostics["detail_pages_fetched"]) + len(detail_batch)
                        self._emit_progress(
                            progress_callback,
                            event="source_detail",
                            source_id=source.id,
                            source_name=source.name,
                            detail_pages=len(detail_batch),
                            page=page_number,
                        )
                        await self._enrich_detail_pages(
                            detail_batch,
                            source,
                            throttle=throttle,
                            cancel_requested=cancel_requested,
                        )
                        remaining_detail_budget = max(0, remaining_detail_budget - len(detail_batch))

                    all_jobs.extend(prepared_jobs)
                    new_or_changed_count = len(prepared_jobs) - known_count
                    self._emit_progress(
                        progress_callback,
                        event="source_page",
                        source_id=source.id,
                        source_name=source.name,
                        page=page_number,
                        jobs_kept=len(prepared_jobs),
                        known_jobs=known_count,
                        new_or_changed_jobs=new_or_changed_count,
                        total_jobs=len(all_jobs),
                    )

                    if self._is_mostly_known_page(prepared_jobs, known_count, new_or_changed_count):
                        consecutive_known_pages += 1
                    else:
                        consecutive_known_pages = 0

                    if (
                        page_number >= DEFAULT_EARLY_STOP_MIN_PAGES
                        and consecutive_known_pages >= DEFAULT_EARLY_STOP_CONSECUTIVE_PAGES
                    ):
                        diagnostics["stopped_early"] = True
                        self._emit_progress(
                            progress_callback,
                            event="source_early_stop",
                            source_id=source.id,
                            source_name=source.name,
                            page=page_number,
                        )
                        break

                    if not next_url or next_url in seen_page_urls:
                        break
                    next_url = sanitize_source_url(next_url, source.source_type)
                    seen_page_urls.add(next_url)
                    url = next_url
            finally:
                await context.close()

        return all_jobs[:max_jobs], None, None, False

    def _prepare_payload(
        self,
        source: JobSourceConfig,
        payload: dict[str, Any],
        known_jobs: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        prepared = dict(payload)
        prepared["external_id"] = self.normalizer.derive_external_id(source, prepared)
        prepared["listing_hash"] = self.normalizer.build_listing_hash(source, prepared)
        snapshot = known_jobs.get(str(prepared["external_id"]))

        if snapshot and snapshot.get("description"):
            prepared["description"] = prepared.get("description") or snapshot["description"]
        if snapshot and snapshot.get("employment_text") and not prepared.get("employment_text"):
            prepared["employment_text"] = snapshot["employment_text"]

        prepared["_known_snapshot"] = snapshot
        prepared["_known_listing"] = bool(snapshot and snapshot.get("listing_hash") == prepared["listing_hash"])
        prepared["_requires_detail"] = bool(
            snapshot is None
            or not snapshot.get("description")
            or snapshot.get("listing_hash") != prepared["listing_hash"]
        )
        return prepared

    def _parse_html_jobs(self, html: str, base_url: str, *, parser: str) -> tuple[list[dict[str, Any]], str | None]:
        soup = BeautifulSoup(html, "html.parser")
        if parser == "indeed":
            jobs = self._parse_indeed_html(soup, base_url)
        elif parser == "clearance":
            jobs = self._parse_clearance_html(soup, base_url)
        else:
            jobs = self._parse_generic_html(soup, base_url)
        next_url = self._extract_next_page_url(soup, base_url, parser=parser)
        return self._deduplicate_jobs(jobs), next_url

    def _parse_indeed_html(self, soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        selectors = [
            "[data-jk]",
            "[data-testid='slider_item']",
            "a[href*='/viewjob']",
            "a[href*='clk?jk=']",
        ]
        for selector in selectors:
            for node in soup.select(selector):
                anchor = node if node.name == "a" else node.find("a", href=True)
                if anchor is None:
                    continue
                title = normalize_whitespace(anchor.get_text(" "))
                if not title:
                    continue
                parent = node if node.name != "a" else node.parent
                jobs.append(
                    {
                        "raw_id": node.get("data-jk") or anchor.get("data-jk"),
                        "title": title,
                        "company": self._first_text(parent, [".companyName", "[data-testid='company-name']", "span.companyName"]),
                        "location": self._first_text(parent, [".companyLocation", "[data-testid='text-location']"]),
                        "summary": self._first_text(parent, [".job-snippet", "[data-testid='job-snippet']"]),
                        "url": absolute_url(base_url, anchor.get("href")),
                        "posted_at": self._first_text(parent, [".date", "[data-testid='myJobsStateDate']"]),
                    }
                )
            if jobs:
                break
        jobs.extend(self._extract_json_ld_jobs(soup, base_url))
        return jobs

    def _parse_clearance_html(self, soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for container in soup.select("article, .job, .job-listing, li"):
            anchor = container.find("a", href=True)
            if anchor is None:
                continue
            href = anchor.get("href", "")
            if "job" not in href and "position" not in href and "clearance" not in href:
                continue
            title = normalize_whitespace(anchor.get_text(" "))
            if len(title) < 3:
                continue
            jobs.append(
                {
                    "raw_id": href,
                    "title": title,
                    "company": self._first_text(container, [".company", ".job-company", "[data-testid='company']"]),
                    "location": self._first_text(container, [".location", ".job-location", "[data-testid='location']"]),
                    "summary": self._first_text(container, [".description", ".job-description", "p"]),
                    "url": absolute_url(base_url, href),
                    "employment_text": self._first_text(container, [".employment-type", ".job-type"]),
                }
            )
        jobs.extend(self._extract_json_ld_jobs(soup, base_url))
        return jobs

    def _parse_generic_html(self, soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
        jobs = self._extract_json_ld_jobs(soup, base_url)
        job_like_containers = soup.select("article, li, .job, .posting, .job-listing, .result")
        for container in job_like_containers:
            anchor = container.find("a", href=True)
            if anchor is None:
                continue
            href = anchor.get("href", "")
            title = normalize_whitespace(anchor.get_text(" "))
            if len(title) < 4:
                continue
            if not any(keyword in href.lower() for keyword in ["job", "career", "opening", "position", "posting", "opportunit"]):
                continue
            jobs.append(
                {
                    "raw_id": href,
                    "title": title,
                    "company": self._first_text(container, [".company", ".posting-company", "[itemprop='hiringOrganization']"]),
                    "location": self._first_text(container, [".location", "[itemprop='jobLocation']"]),
                    "summary": self._first_text(container, [".description", ".summary", "p"]),
                    "url": absolute_url(base_url, href),
                }
            )
        return jobs

    def _extract_next_page_url(self, soup: BeautifulSoup, base_url: str, *, parser: str) -> str | None:
        selectors = [
            "link[rel='next']",
            "a[rel='next']",
            "a[data-testid='pagination-page-next']",
            "a[aria-label='Next']",
            "a[aria-label='Next Page']",
            "a[aria-label*='Next']",
            ".pagination a[aria-label*='Next']",
        ]
        for selector in selectors:
            node = soup.select_one(selector)
            href = node.get("href") if node else None
            if href:
                return absolute_url(base_url, href)

        for anchor in soup.select("a[href]"):
            label = normalize_whitespace(
                " ".join(
                    filter(
                        None,
                        [
                            anchor.get_text(" "),
                            anchor.get("aria-label", ""),
                            anchor.get("title", ""),
                        ],
                    )
                )
            ).casefold()
            if label in {"next", "next page", "older", "more jobs"} or label.startswith("next "):
                return absolute_url(base_url, anchor.get("href"))

        if parser == "indeed":
            for anchor in soup.select("a[href*='start=']"):
                label = normalize_whitespace(anchor.get("aria-label") or anchor.get_text(" ")).casefold()
                if "next" in label:
                    return absolute_url(base_url, anchor.get("href"))
        return None

    def _extract_json_ld_jobs(self, soup: BeautifulSoup, base_url: str) -> list[dict[str, Any]]:
        jobs: list[dict[str, Any]] = []
        for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
            raw_text = script.string or script.get_text()
            if not raw_text:
                continue
            try:
                payload = json.loads(raw_text)
            except json.JSONDecodeError:
                continue
            records = payload if isinstance(payload, list) else [payload]
            for record in records:
                jobs.extend(self._job_payloads_from_json_ld(record, base_url))
        return jobs

    def _job_payloads_from_json_ld(self, record: dict[str, Any], base_url: str) -> list[dict[str, Any]]:
        if not isinstance(record, dict):
            return []
        record_type = str(record.get("@type", "")).casefold()
        if record_type == JSON_LD_JOB_POSTING:
            company = record.get("hiringOrganization") or {}
            location = record.get("jobLocation") or {}
            return [
                {
                    "raw_id": record.get("identifier") or record.get("url"),
                    "title": record.get("title"),
                    "company": company.get("name") if isinstance(company, dict) else "",
                    "location": self._flatten_json_ld_location(location),
                    "description": strip_html(record.get("description")),
                    "url": absolute_url(base_url, record.get("url")),
                    "posted_at": record.get("datePosted"),
                    "job_type": record.get("employmentType"),
                }
            ]
        graph = record.get("@graph")
        if isinstance(graph, list):
            jobs: list[dict[str, Any]] = []
            for item in graph:
                jobs.extend(self._job_payloads_from_json_ld(item, base_url))
            return jobs
        return []

    async def _enrich_detail_pages(
        self,
        jobs: list[dict[str, Any]],
        source: JobSourceConfig,
        *,
        throttle: SourceThrottle,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        candidates = [job for job in jobs if job.get("url") and job.get("_requires_detail")]
        if not candidates:
            return
        self._raise_if_cancelled(cancel_requested)
        semaphore = asyncio.Semaphore(DEFAULT_DETAIL_FETCH_CONCURRENCY)
        allow_dynamic_detail_fallback = self._determine_source_type(source) not in {"indeed"} and not source.use_browser_profile

        async def enrich(job: dict[str, Any]) -> None:
            async with semaphore:
                self._raise_if_cancelled(cancel_requested)
                try:
                    response = await self._request_text(
                        source,
                        job["url"],
                        throttle=throttle,
                        conditional=False,
                        cancel_requested=cancel_requested,
                    )
                    soup = BeautifulSoup(response.text, "html.parser")
                except httpx.HTTPStatusError as exc:
                    status_code = exc.response.status_code if exc.response is not None else None
                    if status_code not in {403, 429} or not allow_dynamic_detail_fallback:
                        return
                    logger.info(
                        "Falling back to Playwright for detail page %s after HTTP %s",
                        job["url"],
                        status_code,
                    )
                    dynamic_html = await self._fetch_dynamic_html(
                        source,
                        job["url"],
                        cancel_requested=cancel_requested,
                    )
                    if not dynamic_html:
                        return
                    if self._looks_like_security_check(dynamic_html):
                        return
                    soup = BeautifulSoup(dynamic_html, "html.parser")
                except Exception:
                    return
                primary = soup.select_one(".jobDescriptionText, article, main, [role='main'], .posting, .job-description")
                if primary:
                    job["description"] = normalize_whitespace(primary.get_text(" "))
                if not job.get("company"):
                    job["company"] = self._first_text(soup, [".company", "[itemprop='hiringOrganization']"])
                if not job.get("location"):
                    job["location"] = self._first_text(soup, [".location", "[itemprop='jobLocation']"])

        await asyncio.gather(*(enrich(job) for job in candidates))

    async def _request_json(
        self,
        source: JobSourceConfig,
        url: str,
        *,
        throttle: SourceThrottle,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> tuple[Any, httpx.Response]:
        response = await self._request_text(
            source,
            url,
            throttle=throttle,
            cancel_requested=cancel_requested,
        )
        if response.status_code == 304:
            return {}, response
        response.raise_for_status()
        return response.json(), response

    async def _request_text(
        self,
        source: JobSourceConfig,
        url: str,
        *,
        throttle: SourceThrottle,
        conditional: bool = True,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> httpx.Response:
        headers = dict(DEFAULT_HEADERS)
        headers.update(source.headers)
        if conditional:
            if source.etag:
                headers["If-None-Match"] = source.etag
            if source.last_modified:
                headers["If-Modified-Since"] = source.last_modified

        last_error: Exception | None = None
        for attempt in range(DEFAULT_REQUEST_MAX_RETRIES + 1):
            self._raise_if_cancelled(cancel_requested)
            await throttle.wait()
            self._raise_if_cancelled(cancel_requested)
            try:
                async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
                    response = await client.get(url)
            except httpx.RequestError as exc:
                last_error = exc
                if attempt >= DEFAULT_REQUEST_MAX_RETRIES:
                    raise
                await self._sleep_with_backoff(source, attempt, cancel_requested=cancel_requested)
                continue

            if response.status_code in {200, 304}:
                return response
            if response.status_code in RETRYABLE_STATUS_CODES and attempt < DEFAULT_REQUEST_MAX_RETRIES:
                await self._sleep_with_backoff(source, attempt, cancel_requested=cancel_requested)
                continue
            response.raise_for_status()

        if last_error:
            raise last_error
        raise RuntimeError(f"Request failed for {url}")

    @staticmethod
    def _emit_progress(
        progress_callback: Callable[[dict[str, Any]], None] | None,
        **event: Any,
    ) -> None:
        if progress_callback is None:
            return
        try:
            progress_callback(event)
        except Exception:
            logger.debug("Ignoring scan progress callback failure.", exc_info=True)

    async def _sleep_with_backoff(
        self,
        source: JobSourceConfig,
        attempt: int,
        *,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        self._raise_if_cancelled(cancel_requested)
        base_delay = max(source.request_delay_ms, 250) / 1000
        await asyncio.sleep(base_delay * (DEFAULT_REQUEST_BACKOFF_MULTIPLIER ** attempt))
        self._raise_if_cancelled(cancel_requested)

    async def _fetch_dynamic_html(
        self,
        source: JobSourceConfig,
        url: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str | None:
        self._raise_if_cancelled(cancel_requested)
        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except Exception:
            logger.warning("Playwright is unavailable; falling back to static scraping for %s", url)
            return None

        try:
            async with async_playwright() as playwright:
                context, page = await self._open_browser_context(playwright, source)
                try:
                    return await self._navigate_and_capture_browser_html(
                        page,
                        source,
                        url,
                        progress_callback=progress_callback,
                        cancel_requested=cancel_requested,
                    )
                finally:
                    await context.close()
        except Exception as exc:
            logger.warning("Playwright fetch failed for %s: %s", url, exc)
            return None

    async def _open_browser_context(self, playwright, source: JobSourceConfig):
        if source.use_browser_profile:
            context = await playwright.chromium.launch_persistent_context(
                user_data_dir=str(self._browser_profile_dir(source)),
                headless=False,
                user_agent=DEFAULT_HEADERS["User-Agent"],
                locale="en-US",
                extra_http_headers={"Accept-Language": DEFAULT_HEADERS["Accept-Language"]},
                viewport={"width": 1440, "height": 1024},
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            page = context.pages[0] if context.pages else await context.new_page()
            with contextlib.suppress(Exception):
                await page.bring_to_front()
            await page.add_init_script(
                """
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                window.chrome = window.chrome || { runtime: {} };
                """
            )
            return context, page

        browser = await playwright.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
            ],
        )
        context = await browser.new_context(
            user_agent=DEFAULT_HEADERS["User-Agent"],
            locale="en-US",
            extra_http_headers={"Accept-Language": DEFAULT_HEADERS["Accept-Language"]},
            viewport={"width": 1440, "height": 1024},
        )
        page = await context.new_page()
        await page.add_init_script(
            """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            window.chrome = window.chrome || { runtime: {} };
            """
        )
        return context, page

    async def _navigate_and_capture_browser_html(
        self,
        page,
        source: JobSourceConfig,
        url: str,
        *,
        progress_callback: Callable[[dict[str, Any]], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        from playwright.async_api import TimeoutError as PlaywrightTimeoutError

        self._raise_if_cancelled(cancel_requested)
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=int(DEFAULT_HTTP_TIMEOUT * 1000))
        except PlaywrightTimeoutError:
            logger.warning("Playwright goto timed out for %s; capturing whatever loaded.", url)
        selectors = [
            "[data-jk]",
            "[data-testid='slider_item']",
            ".jobsearch-ResultsList",
            "main",
            "article",
        ]
        for selector in selectors:
            with contextlib.suppress(Exception):
                await page.wait_for_selector(selector, timeout=2500)
                break
        await page.wait_for_timeout(1200)
        if source.use_browser_profile:
            return await self._wait_for_manual_browser_clearance(
                source,
                page,
                progress_callback,
                cancel_requested=cancel_requested,
            )
        return await page.content()

    async def _wait_for_manual_browser_clearance(
        self,
        source: JobSourceConfig,
        page,
        progress_callback: Callable[[dict[str, Any]], None] | None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> str:
        html = await page.content()
        if not self._looks_like_security_check(html):
            return html
        self._emit_progress(
            progress_callback,
            event="source_browser_assist",
            source_id=source.id,
            source_name=source.name,
            wait_seconds=DEFAULT_BROWSER_CHALLENGE_WAIT_SECONDS,
        )
        deadline = time.monotonic() + DEFAULT_BROWSER_CHALLENGE_WAIT_SECONDS
        while time.monotonic() < deadline:
            self._raise_if_cancelled(cancel_requested)
            await page.wait_for_timeout(2000)
            html = await page.content()
            if not self._looks_like_security_check(html):
                return html
        return html

    @staticmethod
    def _raise_if_cancelled(cancel_requested: Callable[[], bool] | None) -> None:
        if cancel_requested is not None and cancel_requested():
            raise ScanCancelledError("Cancelled by user.")

    @staticmethod
    def _browser_profile_dir(source: JobSourceConfig) -> Path:
        token = source.id if source.id is not None else safe_filename(source.name)
        return BROWSER_PROFILES_DIR / f"source-{token}"

    @staticmethod
    def _should_use_browser_session(source: JobSourceConfig, parser: str) -> bool:
        return source.use_browser_profile and parser in {"indeed", "clearance", "generic"}

    @staticmethod
    def _detail_fetch_budget(source: JobSourceConfig, parser: str) -> int:
        if source.use_browser_profile and parser == "indeed":
            return 0
        return DEFAULT_DETAIL_FETCH_LIMIT

    @staticmethod
    def _should_try_dynamic_fallback(parser: str, status_code: int | None) -> bool:
        return parser in {"indeed", "clearance", "generic"} and status_code in {403, 429}

    @staticmethod
    def _looks_like_security_check(html: str | None) -> bool:
        content = normalize_whitespace(html).casefold()
        if not content:
            return False
        return any(marker in content for marker in SECURITY_CHECK_MARKERS)

    @staticmethod
    def _security_check_message(source: JobSourceConfig, parser: str, *, needs_browser_profile: bool) -> str:
        label = source.name or parser.title()
        if needs_browser_profile:
            return (
                f"{label} is being blocked by an Indeed/Cloudflare security check. "
                "Enable 'Use persistent browser profile' for this source, rescan, and complete the verification in the opened browser window if prompted."
            )
        return (
            f"{label} is still on the Indeed/Cloudflare security check page. "
            "If a browser window opened, complete the verification there and run the scan again."
        )

    @staticmethod
    def _determine_source_type(source: JobSourceConfig) -> str:
        if source.source_type != "auto":
            return source.source_type
        url = source.url.casefold()
        if "greenhouse" in url:
            return "greenhouse"
        if "lever.co" in url:
            return "lever"
        if "indeed." in url:
            return "indeed"
        if "clearance" in url:
            return "clearance"
        return "custom_url"

    @staticmethod
    def _extract_greenhouse_identifier(value: str) -> str | None:
        match = re.search(r"greenhouse(?:\.io|app\.greenhouse\.io)/(?:embed/jobapp|boards|job-boards)?/?([a-z0-9_-]+)", value, re.IGNORECASE)
        if match:
            return match.group(1)
        if re.fullmatch(r"[a-z0-9_-]+", value, re.IGNORECASE):
            return value
        return None

    @staticmethod
    def _extract_lever_identifier(value: str) -> str | None:
        match = re.search(r"(?:api\.lever\.co/v0/postings|jobs\.lever\.co)/([a-z0-9_-]+)", value, re.IGNORECASE)
        if match:
            return match.group(1)
        if re.fullmatch(r"[a-z0-9_-]+", value, re.IGNORECASE):
            return value
        return None

    @staticmethod
    def _deduplicate_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        deduped: list[dict[str, Any]] = []
        seen: set[str] = set()
        for job in jobs:
            key = str(job.get("raw_id") or job.get("url") or f"{job.get('title')}|{job.get('company')}")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(job)
        return deduped

    @staticmethod
    def _is_mostly_known_page(page_jobs: list[dict[str, Any]], known_count: int, new_or_changed_count: int) -> bool:
        if len(page_jobs) < 5:
            return False
        known_ratio = known_count / len(page_jobs)
        return known_ratio >= DEFAULT_EARLY_STOP_KNOWN_RATIO and new_or_changed_count <= max(1, len(page_jobs) // 6)

    @staticmethod
    def _first_text(container, selectors: list[str]) -> str:
        if container is None:
            return ""
        for selector in selectors:
            node = container.select_one(selector) if hasattr(container, "select_one") else None
            if node and node.get_text():
                return normalize_whitespace(node.get_text(" "))
        return ""

    @staticmethod
    def _flatten_json_ld_location(location: Any) -> str:
        if isinstance(location, dict):
            address = location.get("address")
            if isinstance(address, dict):
                pieces = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
                return normalize_whitespace(", ".join(filter(None, pieces)))
            return normalize_whitespace(location.get("name") or "")
        if isinstance(location, list):
            return normalize_whitespace(", ".join(filter(None, [JobFetcher._flatten_json_ld_location(item) for item in location])))
        return normalize_whitespace(str(location or ""))
