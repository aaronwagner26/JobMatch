import asyncio
from datetime import UTC, datetime, timedelta

import httpx

from app.core.engine import JobMatchEngine
from app.core.job_fetcher import JobFetcher, SourceThrottle
from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig, NormalizedJob


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200, headers: dict | None = None) -> None:
        self.text = text
        self.status_code = status_code
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


def _indeed_page(jobs: list[dict], next_href: str | None = None) -> str:
    cards = []
    for job in jobs:
        cards.append(
            f"""
            <div data-jk="{job['raw_id']}">
              <a href="{job['href']}">{job['title']}</a>
              <span class="companyName">{job['company']}</span>
              <div class="companyLocation">{job['location']}</div>
              <div class="job-snippet">{job['summary']}</div>
              <span class="date">{job['posted_at']}</span>
            </div>
            """
        )
    next_link = f'<a rel="next" href="{next_href}">Next</a>' if next_href else ""
    return "<html><body>" + "".join(cards) + next_link + "</body></html>"


def _job_payloads(prefix: str, count: int) -> list[dict]:
    return [
        {
            "raw_id": f"{prefix}-{index}",
            "href": f"/viewjob?jk={prefix}-{index}",
            "title": f"Platform Engineer {prefix}-{index}",
            "company": "Example Co",
            "location": "Remote",
            "summary": f"Python and AWS role {prefix}-{index}",
            "posted_at": "1 day ago",
        }
        for index in range(count)
    ]


def test_fetcher_paginates_and_stops_after_mostly_known_pages() -> None:
    source = JobSourceConfig(
        id=1,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=5,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    page_jobs = [_job_payloads("page1", 6), _job_payloads("page2", 6), _job_payloads("page3", 6), _job_payloads("page4", 6)]
    responses = {
        "https://example.com/jobs": _indeed_page(page_jobs[0], next_href="/jobs?page=2"),
        "https://example.com/jobs?page=2": _indeed_page(page_jobs[1], next_href="/jobs?page=3"),
        "https://example.com/jobs?page=3": _indeed_page(page_jobs[2], next_href="/jobs?page=4"),
        "https://example.com/jobs?page=4": _indeed_page(page_jobs[3], next_href=None),
    }
    known_jobs = {}
    for page in page_jobs[:3]:
        for payload in page:
            prepared = dict(payload)
            prepared["url"] = f"https://example.com{payload['href']}"
            external_id = fetcher.normalizer.derive_external_id(source, prepared)
            listing_hash = fetcher.normalizer.build_listing_hash(source, prepared)
            known_jobs[external_id] = {"listing_hash": listing_hash, "description": "Known description"}

    requested_urls: list[str] = []

    async def fake_request_text(scan_source, url, *, throttle, conditional=True):  # noqa: ANN001
        requested_urls.append(url)
        return FakeResponse(responses[url], headers={"etag": "etag-1"})

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = lambda url: None  # type: ignore[assignment]

    jobs, _, _, _ = asyncio.run(
        fetcher._fetch_search_page(
            source,
            parser="indeed",
            max_jobs=120,
            known_jobs=known_jobs,
            throttle=SourceThrottle(0),
            progress_callback=None,
            diagnostics={"pages_scanned": 0, "detail_pages_fetched": 0, "stopped_early": False},
        )
    )

    assert requested_urls == [
        "https://example.com/jobs",
        "https://example.com/jobs?page=2",
        "https://example.com/jobs?page=3",
    ]
    assert len(jobs) == 18


def test_fetcher_only_enriches_new_or_changed_jobs() -> None:
    source = JobSourceConfig(
        id=2,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=2,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    page = _job_payloads("known", 1) + _job_payloads("new", 1)
    known_payload = dict(page[0])
    known_payload["url"] = f"https://example.com{known_payload['href']}"
    external_id = fetcher.normalizer.derive_external_id(source, known_payload)
    listing_hash = fetcher.normalizer.build_listing_hash(source, known_payload)
    known_jobs = {
        external_id: {
            "listing_hash": listing_hash,
            "description": "Stored job description",
            "employment_text": "Full-time",
        }
    }

    async def fake_request_text(scan_source, url, *, throttle, conditional=True):  # noqa: ANN001
        return FakeResponse(_indeed_page(page))

    captured_batches: list[list[str]] = []

    async def fake_enrich(jobs, scan_source, *, throttle):  # noqa: ANN001
        captured_batches.append([job["external_id"] for job in jobs])

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._enrich_detail_pages = fake_enrich  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = lambda url: None  # type: ignore[assignment]

    jobs, _, _, _ = asyncio.run(
        fetcher._fetch_search_page(
            source,
            parser="indeed",
            max_jobs=20,
            known_jobs=known_jobs,
            throttle=SourceThrottle(0),
            progress_callback=None,
            diagnostics={"pages_scanned": 0, "detail_pages_fetched": 0, "stopped_early": False},
        )
    )

    assert len(captured_batches) == 1
    assert len(captured_batches[0]) == 1
    assert captured_batches[0][0].startswith("new-")
    known_job = next(job for job in jobs if job["external_id"] == external_id)
    assert known_job["description"] == "Stored job description"


def test_scan_source_emits_progress_and_records_metrics() -> None:
    source = JobSourceConfig(
        id=3,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=2,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    page = _job_payloads("first", 2)
    progress_events: list[dict] = []

    async def fake_request_text(scan_source, url, *, throttle, conditional=True):  # noqa: ANN001
        return FakeResponse(_indeed_page(page))

    async def fake_enrich(jobs, scan_source, *, throttle):  # noqa: ANN001
        for job in jobs:
            job["description"] = f"Detailed {job['title']}"

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._enrich_detail_pages = fake_enrich  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = lambda url: None  # type: ignore[assignment]

    result = asyncio.run(
        fetcher.scan_source(
            source,
            max_jobs=20,
            known_jobs={},
            progress_callback=lambda event: progress_events.append(event),
        )
    )

    assert result.status == "ok"
    assert result.pages_scanned == 1
    assert result.detail_pages_fetched == 2
    assert any(event["event"] == "source_page" for event in progress_events)
    assert any(event["event"] == "source_detail" for event in progress_events)


def test_scan_source_falls_back_to_playwright_after_403() -> None:
    source = JobSourceConfig(
        id=4,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=1,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    progress_events: list[dict] = []

    async def fake_request_text(scan_source, url, *, throttle, conditional=True):  # noqa: ANN001
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    async def fake_dynamic_html(url: str) -> str | None:
        return _indeed_page(_job_payloads("dynamic", 2))

    async def fake_enrich(jobs, scan_source, *, throttle):  # noqa: ANN001
        for job in jobs:
            job["description"] = f"Detailed {job['title']}"

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[assignment]
    fetcher._enrich_detail_pages = fake_enrich  # type: ignore[method-assign]

    jobs, _, _, _ = asyncio.run(
        fetcher._fetch_search_page(
            source,
            parser="indeed",
            max_jobs=20,
            known_jobs={},
            throttle=SourceThrottle(0),
            progress_callback=lambda event: progress_events.append(event),
            diagnostics={"pages_scanned": 0, "detail_pages_fetched": 0, "stopped_early": False},
        )
    )

    assert len(jobs) == 2
    assert any(event["event"] == "source_fallback" for event in progress_events)


def test_engine_deduplicates_cross_source_jobs_by_canonical_url() -> None:
    newer_time = datetime.now(UTC)
    older_time = newer_time - timedelta(hours=1)
    duplicate_url = "https://boards.example.com/jobs/platform-engineer?utm_source=test"
    first = NormalizedJob(
        id=1,
        source_id=1,
        source_name="Source A",
        source_type="custom_url",
        external_id="a-1",
        title="Platform Engineer",
        company="Example Co",
        location="Remote",
        remote_mode="remote",
        job_type="full-time",
        clearance_terms=[],
        posted_at=None,
        url=duplicate_url,
        description="Short summary",
        summary_text="Short summary",
        skills=["Python"],
        required_skills=["Python"],
        preferred_skills=[],
        experience_years=5.0,
        employment_text="Full-time",
        metadata={"canonical_url": "https://boards.example.com/jobs/platform-engineer"},
        content_hash="hash-a",
        active=True,
        first_seen_at=older_time,
        last_seen_at=older_time,
        last_updated_at=older_time,
    )
    second = NormalizedJob(
        id=2,
        source_id=2,
        source_name="Source B",
        source_type="custom_url",
        external_id="b-1",
        title="Platform Engineer",
        company="Example Co",
        location="Remote",
        remote_mode="remote",
        job_type="full-time",
        clearance_terms=[],
        posted_at=None,
        url=duplicate_url,
        description="Longer summary with Python, AWS, Docker, and Kubernetes experience required.",
        summary_text="Longer summary with Python, AWS, Docker, and Kubernetes experience required.",
        skills=["Python", "AWS", "Docker", "Kubernetes"],
        required_skills=["Python", "AWS"],
        preferred_skills=["Docker", "Kubernetes"],
        experience_years=5.0,
        employment_text="Full-time",
        metadata={"canonical_url": "https://boards.example.com/jobs/platform-engineer"},
        content_hash="hash-b",
        active=True,
        first_seen_at=older_time,
        last_seen_at=newer_time,
        last_updated_at=newer_time,
    )

    deduped = JobMatchEngine._deduplicate_jobs([first, second])

    assert len(deduped) == 1
    assert deduped[0].source_name == "Source B"
