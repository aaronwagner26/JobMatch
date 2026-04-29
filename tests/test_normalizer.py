from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig


def test_job_normalizer_extracts_structure() -> None:
    source = JobSourceConfig(id=1, name="Example Board", source_type="custom_url", url="https://example.com/jobs")
    job = JobNormalizer().normalize(
        source,
        {
            "raw_id": "job-123",
            "title": "Platform Engineer",
            "company": "Example Co",
            "location": "Remote - US",
            "description": (
                "We need a platform engineer with Python, AWS, Docker, Kubernetes, Terraform, "
                "and PostgreSQL. Must have 5+ years experience and active TS/SCI clearance. "
                "This is a full-time remote role."
            ),
            "url": "https://example.com/jobs/job-123",
        },
    )

    assert job.external_id == "job-123"
    assert job.remote_mode == "remote"
    assert job.job_type == "full-time"
    assert job.experience_years == 5.0
    assert "Python" in job.skills
    assert "AWS" in job.skills
    assert "TS/SCI" in job.clearance_terms

