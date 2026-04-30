from __future__ import annotations

from app.core.engine import JobMatchEngine
from app.core.job_fetcher import JobFetcher
from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig, NormalizedJob
from app.db.storage import Storage


def test_import_saved_html_parses_indeed_results_page() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=1,
        name="Indeed Search",
        source_type="indeed",
        url="https://www.indeed.com/jobs?q=system+administrator&l=Remote",
    )
    html = """
    <html>
      <body>
        <div data-jk="abc123">
          <a href="/viewjob?jk=abc123">System Administrator</a>
          <span class="companyName">Example Co</span>
          <div class="companyLocation">Remote</div>
          <div class="job-snippet">Python, Windows, and AWS support role</div>
          <span class="date">1 day ago</span>
        </div>
      </body>
    </html>
    """

    result = fetcher.import_saved_html(source, html, max_jobs=20)

    assert result.status == "manual_import"
    assert len(result.jobs) == 1
    assert result.jobs[0].title == "System Administrator"
    assert result.jobs[0].company == "Example Co"


def test_scheduler_skips_manual_assist_sources() -> None:
    storage = Storage("sqlite+pysqlite:///:memory:")
    engine = JobMatchEngine(storage=storage)
    engine.save_source(
        JobSourceConfig(
            id=None,
            name="Indeed Search",
            source_type="indeed",
            url="https://www.indeed.com/jobs?q=system+administrator&l=Remote",
            enabled=True,
            use_browser_profile=True,
        )
    )
    engine.update_settings({"scheduler_enabled": True, "scheduler_interval_minutes": 180})

    assert engine.should_run_scheduled_scan() is False

    custom = engine.save_source(
        JobSourceConfig(
            id=None,
            name="Company Board",
            source_type="custom_url",
            url="https://example.com/jobs",
            enabled=True,
        )
    )
    assert custom.id is not None

    assert engine.should_run_scheduled_scan() is True


def test_clear_scan_results_resets_cached_jobs_and_scan_state() -> None:
    storage = Storage("sqlite+pysqlite:///:memory:")
    engine = JobMatchEngine(storage=storage)
    source = engine.save_source(
        JobSourceConfig(
            id=None,
            name="Company Board",
            source_type="custom_url",
            url="https://example.com/jobs",
            enabled=True,
        )
    )
    assert source.id is not None

    job = NormalizedJob(
        id=None,
        source_id=source.id,
        source_name=source.name,
        source_type=source.source_type,
        external_id="job-1",
        title="Platform Engineer",
        company="Example Co",
        location="Remote",
        remote_mode="remote",
        job_type="full-time",
        clearance_terms=[],
        posted_at=None,
        url="https://example.com/jobs/platform-engineer",
        description="Python and AWS role",
        summary_text="Python and AWS role",
        skills=["Python", "AWS"],
        required_skills=["Python"],
        preferred_skills=["AWS"],
        experience_years=4.0,
        employment_text="Full-time",
        metadata={},
        content_hash="hash-1",
    )
    storage.upsert_jobs(source, [job])
    scan_id = storage.begin_scan(source.id)
    storage.update_source_scan_state(source.id, status="ok", etag="etag-1", last_modified="Wed, 01 Jan 2025 00:00:00 GMT")
    storage.finish_scan(scan_id, status="ok", jobs_found=1, jobs_created=1)

    assert storage.list_jobs()
    assert storage.list_scans()

    engine.clear_scan_results()

    assert storage.list_jobs() == []
    assert storage.list_scans() == []
    cleared_source = engine.get_source(source.id)
    assert cleared_source is not None
    assert cleared_source.etag is None
    assert cleared_source.last_modified is None
    assert cleared_source.last_scan_at is None
    assert cleared_source.last_status is None
