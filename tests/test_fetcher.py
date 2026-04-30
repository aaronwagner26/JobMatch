import asyncio
import os
from datetime import UTC, datetime, timedelta

import httpx

from app.core.engine import JobMatchEngine
from app.core.job_fetcher import JobFetcher, SourceThrottle
from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig, NormalizedJob
from app.utils.text import canonical_job_url, sanitize_source_url


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
        "https://example.com/jobs": _indeed_page(page_jobs[0], next_href="/jobs?start=10"),
        "https://example.com/jobs?start=10": _indeed_page(page_jobs[1], next_href="/jobs?start=20"),
        "https://example.com/jobs?start=20": _indeed_page(page_jobs[2], next_href="/jobs?start=30"),
        "https://example.com/jobs?start=30": _indeed_page(page_jobs[3], next_href=None),
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

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        requested_urls.append(url)
        return FakeResponse(responses[url], headers={"etag": "etag-1"})

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    async def fake_dynamic_html(scan_source, url, *, progress_callback=None, cancel_requested=None):  # noqa: ANN001
        return None

    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[method-assign]

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
        "https://example.com/jobs?start=10",
        "https://example.com/jobs?start=20",
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

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        return FakeResponse(_indeed_page(page))

    captured_batches: list[list[str]] = []

    async def fake_enrich(jobs, scan_source, *, throttle, cancel_requested=None):  # noqa: ANN001
        captured_batches.append([job["external_id"] for job in jobs])

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._enrich_detail_pages = fake_enrich  # type: ignore[method-assign]
    async def fake_dynamic_html(scan_source, url, *, progress_callback=None, cancel_requested=None):  # noqa: ANN001
        return None

    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[method-assign]

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

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        return FakeResponse(_indeed_page(page))

    async def fake_enrich(jobs, scan_source, *, throttle, cancel_requested=None):  # noqa: ANN001
        for job in jobs:
            job["description"] = f"Detailed {job['title']}"

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._enrich_detail_pages = fake_enrich  # type: ignore[method-assign]
    async def fake_dynamic_html(scan_source, url, *, progress_callback=None, cancel_requested=None):  # noqa: ANN001
        return None

    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[method-assign]

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

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    async def fake_dynamic_html(scan_source, url: str, *, progress_callback=None, cancel_requested=None) -> str | None:  # noqa: ANN001
        return _indeed_page(_job_payloads("dynamic", 2))

    async def fake_enrich(jobs, scan_source, *, throttle, cancel_requested=None):  # noqa: ANN001
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


def test_scan_source_marks_security_check_as_blocked() -> None:
    source = JobSourceConfig(
        id=5,
        name="Indeed Search",
        source_type="indeed",
        url="https://www.indeed.com/jobs?q=system+administrator&l=Remote&cf-turnstile-response=token&vjk=abc123",
        max_pages=1,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    async def fake_dynamic_html(scan_source, url: str, *, progress_callback=None, cancel_requested=None) -> str | None:  # noqa: ANN001
        return """
        <html>
          <head><title>Just a moment...</title></head>
          <body>
            Additional Verification Required
            Your Ray ID for this request is 9f4082767d29f2e9
            Troubleshooting Cloudflare Errors
          </body>
        </html>
        """

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[method-assign]

    result = asyncio.run(fetcher.scan_source(source, max_jobs=20, known_jobs={}))

    assert result.status == "blocked"
    assert result.block_reason == "security_check"
    assert "persistent browser profile" in (result.error or "")
    assert source.url == "https://www.indeed.com/jobs?l=Remote&q=system+administrator"


def test_sanitize_source_url_strips_indeed_challenge_parameters() -> None:
    url = (
        "https://www.indeed.com/jobs?q=system+administrator&l=Remote"
        "&from=searchOnDesktopSerp&cf-turnstile-response=token&vjk=2247cc8ae78b6846"
    )

    assert sanitize_source_url(url, "indeed") == "https://www.indeed.com/jobs?l=Remote&q=system+administrator"


def test_canonical_job_url_preserves_indeed_job_identity() -> None:
    assert canonical_job_url(
        "https://www.indeed.com/pagead/clk?mo=r&ad=-6NYlbfk&vjk=job-a&from=serp"
    ) == "https://www.indeed.com/viewjob?jk=job-a"
    assert canonical_job_url(
        "https://www.indeed.com/rc/clk?jk=job-b&from=vj"
    ) == "https://www.indeed.com/viewjob?jk=job-b"
    assert canonical_job_url(
        "https://www.indeed.com/viewjob?currentJobId=job-c&from=app"
    ) == "https://www.indeed.com/viewjob?jk=job-c"


def test_scan_source_returns_cancelled_when_requested() -> None:
    source = JobSourceConfig(
        id=6,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=1,
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    request_attempted = False

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        nonlocal request_attempted
        request_attempted = True
        return FakeResponse(_indeed_page(_job_payloads("cancel", 1)))

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]

    result = asyncio.run(
        fetcher.scan_source(
            source,
            max_jobs=20,
            known_jobs={},
            cancel_requested=lambda: True,
        )
    )

    assert result.status == "cancelled"
    assert result.error == "Cancelled by user."
    assert request_attempted is False


def test_scan_source_rejects_linkedin_company_jobs_pages() -> None:
    source = JobSourceConfig(
        id=10,
        name="Netflix on LinkedIn",
        source_type="custom_url",
        url="https://www.linkedin.com/company/netflix/jobs",
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())
    request_attempted = False

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        nonlocal request_attempted
        request_attempted = True
        return FakeResponse("<html></html>")

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]

    result = asyncio.run(fetcher.scan_source(source, max_jobs=20, known_jobs={}))

    assert result.status == "error"
    assert "LinkedIn company/job pages are not supported" in (result.error or "")
    assert request_attempted is False


def test_scan_source_rejects_browser_capture_sources() -> None:
    source = JobSourceConfig(
        id=11,
        name="Capture: Netflix (LinkedIn)",
        source_type="browser_capture",
        url="https://www.linkedin.com/company/netflix/jobs",
        request_delay_ms=0,
    )
    fetcher = JobFetcher(JobNormalizer())

    result = asyncio.run(fetcher.scan_source(source, max_jobs=20, known_jobs={}))

    assert result.status == "error"
    assert "browser-capture only" in (result.error or "")


def test_indeed_detail_enrichment_skips_browser_fallback_after_block() -> None:
    source = JobSourceConfig(
        id=7,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=1,
        request_delay_ms=0,
        use_browser_profile=True,
    )
    fetcher = JobFetcher(JobNormalizer())
    dynamic_attempts = 0
    jobs = [{"url": "https://example.com/viewjob?jk=abc123", "_requires_detail": True}]

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        request = httpx.Request("GET", url)
        response = httpx.Response(403, request=request)
        raise httpx.HTTPStatusError("Forbidden", request=request, response=response)

    async def fake_dynamic_html(scan_source, url, *, progress_callback=None, cancel_requested=None):  # noqa: ANN001
        nonlocal dynamic_attempts
        dynamic_attempts += 1
        return "<html></html>"

    fetcher._request_text = fake_request_text  # type: ignore[method-assign]
    fetcher._fetch_dynamic_html = fake_dynamic_html  # type: ignore[method-assign]

    asyncio.run(fetcher._enrich_detail_pages(jobs, source, throttle=SourceThrottle(0)))

    assert dynamic_attempts == 0


def test_indeed_browser_profile_uses_browser_session_for_results_pages() -> None:
    source = JobSourceConfig(
        id=8,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        max_pages=2,
        request_delay_ms=0,
        use_browser_profile=True,
    )
    fetcher = JobFetcher(JobNormalizer())
    session_used = False
    request_attempted = False

    async def fake_browser_session(
        scan_source,
        *,
        parser,
        max_jobs,
        known_jobs,
        throttle,
        progress_callback,
        diagnostics,
        cancel_requested=None,
    ):
        nonlocal session_used
        session_used = True
        diagnostics["pages_scanned"] = 1
        return (
            [
                {
                    "raw_id": "browser-1",
                    "title": "Browser Profile Role",
                    "company": "Example Co",
                    "location": "Remote",
                    "summary": "Python and AWS role",
                    "url": "https://example.com/viewjob?jk=browser-1",
                }
            ],
            None,
            None,
            False,
        )

    async def fake_request_text(scan_source, url, *, throttle, conditional=True, cancel_requested=None):  # noqa: ANN001
        nonlocal request_attempted
        request_attempted = True
        return FakeResponse(_indeed_page(_job_payloads("http", 1)))

    fetcher._fetch_search_page_via_browser_session = fake_browser_session  # type: ignore[method-assign]
    fetcher._request_text = fake_request_text  # type: ignore[method-assign]

    result = asyncio.run(fetcher.scan_source(source, max_jobs=20, known_jobs={}))

    assert result.status == "ok"
    assert session_used is True
    assert request_attempted is False


def test_wait_for_manual_browser_clearance_retries_during_navigation() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=9,
        name="Indeed Search",
        source_type="indeed",
        url="https://example.com/jobs",
        request_delay_ms=0,
    )
    progress_events: list[dict] = []

    class FakePage:
        def __init__(self) -> None:
            self.calls = 0

        async def content(self) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("Page.content: Unable to retrieve content because the page is navigating and changing the content.")
            if self.calls == 2:
                return "<html><body>Additional Verification Required</body></html>"
            return "<html><body><main>Jobs loaded</main></body></html>"

        async def wait_for_load_state(self, state, timeout):  # noqa: ANN001
            return None

        async def wait_for_timeout(self, timeout):  # noqa: ANN001
            return None

    html = asyncio.run(
        fetcher._wait_for_manual_browser_clearance(
            source,
            FakePage(),
            lambda event: progress_events.append(event),
        )
    )

    assert "Jobs loaded" in html
    assert any(event["event"] == "source_browser_assist" for event in progress_events)


def test_persistent_browser_launch_options_use_override(tmp_path) -> None:
    fake_browser = tmp_path / "chrome.exe"
    fake_browser.write_text("", encoding="utf-8")
    original = os.environ.get("JOBMATCH_BROWSER_EXECUTABLE")
    os.environ["JOBMATCH_BROWSER_EXECUTABLE"] = str(fake_browser)
    try:
        options, label = JobFetcher._persistent_browser_launch_options()
    finally:
        if original is None:
            os.environ.pop("JOBMATCH_BROWSER_EXECUTABLE", None)
        else:
            os.environ["JOBMATCH_BROWSER_EXECUTABLE"] = original

    assert options["executable_path"] == str(fake_browser)
    assert label == "chrome"


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
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_interval=None,
        salary_text=None,
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
        salary_min=None,
        salary_max=None,
        salary_currency=None,
        salary_interval=None,
        salary_text=None,
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


def test_parse_clearance_search_page_uses_site_specific_selectors() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source_url = "https://www.clearancejobs.com/jobs?remote=1&keywords=information+technology&limit=50&sort_info=timestamp+desc"
    html = """
    <html>
      <body>
        <div class="job-search-list-item-desktop">
          <a class="job-search-list-item-desktop__job-name" href="/jobs/8890517/systems-admin-m365-admin-migration-102119">
            Systems Admin - M365 Admin - Migration 102119
          </a>
          <div class="job-search-list-item-desktop__company-name">Information Technology Engineering Corporation</div>
          <div class="job-search-list-item-desktop__location">Remote/Hybrid United States (On-Site/Office)</div>
          <div class="job-search-list-item-desktop__description">
            Systems Admin-M365 Admin-Migration Location: Remote Required Clearance: Top Secret/DOE Q
          </div>
          <div class="job-search-list-item-desktop__footer">
            <div><div>Remote/Hybrid United States (On-Site/Office)</div></div>
            <div><div>Posted today</div><div>Unspecified</div><div>None</div></div>
            <div></div>
          </div>
        </div>
        <div class="job-search-pagination">
          <div class="cj-pagination">
            <button class="btn--selected btn">1</button>
            <button class="btn">2</button>
            <button class="btn btn--next"></button>
          </div>
        </div>
      </body>
    </html>
    """

    jobs, next_url = fetcher._parse_html_jobs(html, source_url, parser="clearance")

    assert len(jobs) == 1
    assert jobs[0]["title"] == "Systems Admin - M365 Admin - Migration 102119"
    assert jobs[0]["company"] == "Information Technology Engineering Corporation"
    assert jobs[0]["location"] == "Remote/Hybrid United States (On-Site/Office)"
    assert "Top Secret/DOE Q" in jobs[0]["requirements_text"]
    assert jobs[0]["posted_at"] == "Posted today"
    assert next_url == (
        "https://www.clearancejobs.com/jobs?remote=1&keywords=information+technology"
        "&limit=50&sort_info=timestamp+desc&page=2"
    )


def test_parse_clearance_detail_payload_prefers_requirements_and_description() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=12,
        name="ClearanceJobs Search",
        source_type="clearance",
        url="https://www.clearancejobs.com/jobs/8890517/systems-admin-m365-admin-migration-102119",
        request_delay_ms=0,
    )
    html = """
    <html>
      <body>
        <h1 class="job-view-header-content__top__job-name">Systems Admin - M365 Admin - Migration 102119</h1>
        <h2 class="job-view-header-content__top__job-company">Information Technology Engineering Corporation</h2>
        <div class="job-info">
          <h3 class="job-section-title">Job Requirements</h3>
          <div class="job-fit__nonSkills--required">
            <div class="job-fit__nonSkills--location"><span class="el-tag__content">Remote</span></div>
            <div class="job-fit__nonSkills--clearance">
              <span class="el-tag__content">Clearance Unspecified</span>
              <span class="el-tag__content">Polygraph None</span>
            </div>
            <div class="job-fit__nonSkills--salary">
              <span class="el-tag__content">Salary not specified</span>
            </div>
          </div>
        </div>
        <div class="job-description">
          <h3 class="job-section-title">Job Description</h3>
          <p>Location: Remote</p>
          <p>Required Clearance: Top Secret/DOE Q</p>
        </div>
      </body>
    </html>
    """

    payload = fetcher._parse_job_detail_payload(html, source.url, source)

    assert payload is not None
    assert payload["title"] == "Systems Admin - M365 Admin - Migration 102119"
    assert payload["company"] == "Information Technology Engineering Corporation"
    assert payload["location"] == "Remote"
    assert "Required Clearance: Top Secret/DOE Q" in payload["description"]
    assert "Clearance Unspecified" in payload["requirements_text"]
    assert payload["salary_text"] == ""


def test_prepare_payload_requires_detail_for_known_listing_missing_salary() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=13,
        name="ClearanceJobs Search",
        source_type="clearance",
        url="https://www.clearancejobs.com/jobs?remote=1&keywords=information+technology",
        request_delay_ms=0,
    )
    payload = {
        "raw_id": "job-1",
        "title": "Senior Cloud Network Engineer (AWS)",
        "company": "Leidos",
        "location": "Remote",
        "summary": "AWS networking and federal cloud work",
        "url": "https://www.clearancejobs.com/jobs/8887968/senior-cloud-network-engineer-aws",
    }
    listing_hash = fetcher.normalizer.build_listing_hash(source, payload)
    known_jobs = {
        "job-1": {
            "listing_hash": listing_hash,
            "description": "Existing stored description",
            "employment_text": "",
            "salary_text": "",
        }
    }

    prepared = fetcher._prepare_payload(source, payload, known_jobs)

    assert prepared["_known_listing"] is True
    assert prepared["_requires_detail"] is True
