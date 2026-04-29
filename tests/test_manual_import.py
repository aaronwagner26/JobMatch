from __future__ import annotations

from app.core.engine import JobMatchEngine
from app.core.job_fetcher import JobFetcher
from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig
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
