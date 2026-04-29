from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Any

import httpx
from bs4 import BeautifulSoup

from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig, NormalizedJob, ScanResult
from app.utils.config import DEFAULT_DETAIL_FETCH_LIMIT, DEFAULT_HEADERS, DEFAULT_HTTP_TIMEOUT
from app.utils.text import absolute_url, normalize_whitespace, strip_html

logger = logging.getLogger(__name__)

JSON_LD_JOB_POSTING = "jobposting"


class JobFetcher:
    def __init__(self, normalizer: JobNormalizer) -> None:
        self.normalizer = normalizer

    async def scan_source(self, source: JobSourceConfig, max_jobs: int = 120) -> ScanResult:
        source_type = self._determine_source_type(source)
        source.source_type = source_type
        try:
            raw_jobs, etag, last_modified, not_modified = await self._fetch_jobs(source, source_type, max_jobs=max_jobs)
            if not_modified:
                return ScanResult(source=source, status="not_modified", response_etag=etag, response_last_modified=last_modified)

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
            )
        except Exception as exc:
            logger.exception("Source scan failed for %s", source.name)
            return ScanResult(source=source, status="error", error=str(exc))

    async def _fetch_jobs(
        self,
        source: JobSourceConfig,
        source_type: str,
        *,
        max_jobs: int,
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        if source_type == "greenhouse":
            return await self._fetch_greenhouse(source)
        if source_type == "lever":
            return await self._fetch_lever(source)
        if source_type == "indeed":
            return await self._fetch_search_page(source, parser="indeed", max_jobs=max_jobs)
        if source_type == "clearance":
            return await self._fetch_search_page(source, parser="clearance", max_jobs=max_jobs)
        return await self._fetch_search_page(source, parser="generic", max_jobs=max_jobs)

    async def _fetch_greenhouse(self, source: JobSourceConfig) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        identifier = source.identifier or self._extract_greenhouse_identifier(source.url)
        if not identifier:
            raise ValueError("Greenhouse source requires a board token or board URL.")
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{identifier}/jobs?content=true"
        payload, response = await self._request_json(source, endpoint)
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

    async def _fetch_lever(self, source: JobSourceConfig) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        identifier = source.identifier or self._extract_lever_identifier(source.url)
        if not identifier:
            raise ValueError("Lever source requires a company slug or postings URL.")
        endpoint = f"https://api.lever.co/v0/postings/{identifier}?mode=json"
        payload, response = await self._request_json(source, endpoint)
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
    ) -> tuple[list[dict[str, Any]], str | None, str | None, bool]:
        response = await self._request_text(source, source.url)
        if response.status_code == 304:
            return [], response.headers.get("etag"), response.headers.get("last-modified"), True
        html = response.text
        jobs = self._parse_html_jobs(html, source.url, parser=parser)
        if (source.use_playwright or not jobs) and parser in {"indeed", "clearance", "generic"}:
            dynamic_html = await self._fetch_dynamic_html(source.url)
            if dynamic_html:
                jobs = self._parse_html_jobs(dynamic_html, source.url, parser=parser) or jobs
        await self._enrich_detail_pages(jobs[:DEFAULT_DETAIL_FETCH_LIMIT], source)
        return jobs[:max_jobs], response.headers.get("etag"), response.headers.get("last-modified"), False

    def _parse_html_jobs(self, html: str, base_url: str, *, parser: str) -> list[dict[str, Any]]:
        if parser == "indeed":
            jobs = self._parse_indeed_html(html, base_url)
        elif parser == "clearance":
            jobs = self._parse_clearance_html(html, base_url)
        else:
            jobs = self._parse_generic_html(html, base_url)
        return self._deduplicate_jobs(jobs)

    def _parse_indeed_html(self, html: str, base_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
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

    def _parse_clearance_html(self, html: str, base_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
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

    def _parse_generic_html(self, html: str, base_url: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
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

    async def _enrich_detail_pages(self, jobs: list[dict[str, Any]], source: JobSourceConfig) -> None:
        candidates = [job for job in jobs if job.get("url") and not job.get("description")]
        if not candidates:
            return
        semaphore = asyncio.Semaphore(5)

        async def enrich(job: dict[str, Any]) -> None:
            async with semaphore:
                try:
                    response = await self._request_text(source, job["url"], conditional=False)
                except Exception:
                    return
                soup = BeautifulSoup(response.text, "html.parser")
                primary = soup.select_one(".jobDescriptionText, article, main, [role='main'], .posting, .job-description")
                if primary:
                    job["description"] = normalize_whitespace(primary.get_text(" "))
                if not job.get("company"):
                    job["company"] = self._first_text(soup, [".company", "[itemprop='hiringOrganization']"])
                if not job.get("location"):
                    job["location"] = self._first_text(soup, [".location", "[itemprop='jobLocation']"])

        await asyncio.gather(*(enrich(job) for job in candidates))

    async def _request_json(self, source: JobSourceConfig, url: str) -> tuple[Any, httpx.Response]:
        response = await self._request_text(source, url)
        if response.status_code == 304:
            return {}, response
        response.raise_for_status()
        return response.json(), response

    async def _request_text(self, source: JobSourceConfig, url: str, *, conditional: bool = True) -> httpx.Response:
        headers = dict(DEFAULT_HEADERS)
        headers.update(source.headers)
        if conditional:
            if source.etag:
                headers["If-None-Match"] = source.etag
            if source.last_modified:
                headers["If-Modified-Since"] = source.last_modified
        async with httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT, follow_redirects=True, headers=headers) as client:
            response = await client.get(url)
        if response.status_code not in {200, 304}:
            response.raise_for_status()
        return response

    async def _fetch_dynamic_html(self, url: str) -> str | None:
        try:
            from playwright.async_api import async_playwright
        except Exception:
            logger.warning("Playwright is unavailable; falling back to static scraping for %s", url)
            return None

        try:
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(headless=True)
                page = await browser.new_page()
                await page.goto(url, wait_until="networkidle", timeout=int(DEFAULT_HTTP_TIMEOUT * 1000))
                html = await page.content()
                await browser.close()
                return html
        except Exception as exc:
            logger.warning("Playwright fetch failed for %s: %s", url, exc)
            return None

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
