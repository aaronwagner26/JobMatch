from __future__ import annotations

from app.core.engine import JobMatchEngine
from app.core.types import FilterCriteria, JobSourceConfig, NormalizedJob
from app.db.storage import Storage


def _sample_job(source, external_id: str, title: str) -> NormalizedJob:
    return NormalizedJob(
        id=None,
        source_id=source.id,
        source_name=source.name,
        source_type=source.source_type,
        external_id=external_id,
        title=title,
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
        url=f"https://example.com/jobs/{external_id}",
        description="Python and AWS role",
        summary_text="Python and AWS role",
        skills=["Python", "AWS"],
        required_skills=["Python"],
        preferred_skills=["AWS"],
        experience_years=4.0,
        employment_text="Full-time",
        metadata={},
        content_hash=f"hash-{external_id}",
    )


def test_application_state_tracking_and_filters() -> None:
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
    storage.upsert_jobs(
        source,
        [
            _sample_job(source, "job-1", "Platform Engineer"),
            _sample_job(source, "job-2", "Cloud Engineer"),
        ],
    )
    jobs = storage.list_jobs()
    first = next(job for job in jobs if job.external_id == "job-1")
    second = next(job for job in jobs if job.external_id == "job-2")

    opened = engine.mark_job_opened_for_apply(first.id or 0)
    assert opened.application_status == "pending"
    assert opened.application_confirmation_needed is True

    pending = engine.list_jobs_pending_confirmation()
    assert [job.id for job in pending] == [first.id]

    not_applied_yet = engine.list_filtered_jobs(FilterCriteria(application_state="not_applied_yet"))
    assert {job.id for job in not_applied_yet} == {first.id, second.id}

    applied = engine.set_job_application_state(first.id or 0, "applied")
    assert applied.application_status == "applied"
    assert applied.application_confirmation_needed is False

    applied_only = engine.list_filtered_jobs(FilterCriteria(application_state="applied"))
    assert [job.id for job in applied_only] == [first.id]

    uninterested = engine.set_job_application_state(second.id or 0, "not_interested")
    assert uninterested.application_status == "not_interested"

    not_interested_only = engine.list_filtered_jobs(FilterCriteria(application_state="not_interested"))
    assert [job.id for job in not_interested_only] == [second.id]

    pending_after_resolution = engine.list_jobs_pending_confirmation()
    assert pending_after_resolution == []


def test_mark_job_opened_does_not_override_applied_state() -> None:
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
    storage.upsert_jobs(source, [_sample_job(source, "job-1", "Platform Engineer")])
    job = storage.list_jobs()[0]

    engine.set_job_application_state(job.id or 0, "applied")
    reopened = engine.mark_job_opened_for_apply(job.id or 0)

    assert reopened.application_status == "applied"
    assert reopened.application_confirmation_needed is False
