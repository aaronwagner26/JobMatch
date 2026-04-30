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
                "This is a full-time remote role. Salary range: $145,000 - $175,000 per year."
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
    assert job.salary_min == 145000.0
    assert job.salary_max == 175000.0
    assert job.salary_interval == "year"
    assert job.salary_text == "$145,000 - $175,000/yr"


def test_job_normalizer_does_not_flag_secret_without_clearance_context() -> None:
    source = JobSourceConfig(id=1, name="Example Board", source_type="custom_url", url="https://example.com/jobs")
    job = JobNormalizer().normalize(
        source,
        {
            "raw_id": "job-456",
            "title": "Security Engineer",
            "company": "Example Co",
            "location": "Remote",
            "description": (
                "You will work on secret-management automation, secrets rotation, and vault integrations. "
                "Candidates must have Python and AWS experience."
            ),
            "url": "https://example.com/jobs/job-456",
        },
    )

    assert job.clearance_terms == []


def test_job_normalizer_merges_optional_llm_enrichment() -> None:
    class FakeEnricher:
        def enrich_job(self, *, title, company, location, description, extracted):  # noqa: ANN001
            assert title == "Systems Engineer"
            assert company == "Example Co"
            assert location == "Remote"
            assert extracted["skills"]
            return {
                "required_skills": ["PowerShell", "Active Directory"],
                "preferred_skills": ["VMware"],
                "skills": ["PowerShell", "Active Directory", "VMware"],
                "clearance_terms": ["Secret"],
                "salary_text": "$120,000 - $140,000 per year",
                "job_type": "full-time",
                "remote_mode": "remote",
                "experience_years_hint": 6,
                "short_summary": "Windows infrastructure role with endpoint and virtualization ownership.",
            }

    source = JobSourceConfig(id=1, name="Example Board", source_type="custom_url", url="https://example.com/jobs")
    job = JobNormalizer().normalize(
        source,
        {
            "raw_id": "job-789",
            "title": "Systems Engineer",
            "company": "Example Co",
            "location": "Remote",
            "description": (
                "We need a systems engineer with Python and AWS experience. "
                "Must have 5+ years experience."
            ),
            "url": "https://example.com/jobs/job-789",
        },
        llm_enricher=FakeEnricher(),
    )

    assert "PowerShell" in job.required_skills
    assert "VMware" in job.preferred_skills
    assert "Secret" in job.clearance_terms
    assert job.salary_text == "$120,000 - $140,000/yr"
    assert job.experience_years == 6.0
    assert "LLM summary:" in job.summary_text
