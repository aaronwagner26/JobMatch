from pathlib import Path

from app.core.engine import JobMatchEngine
from app.core.resume_parser import ResumeParser
from app.db.storage import Storage


def test_resume_parser_extracts_titles_certifications_and_clearance(tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(
        "\n".join(
            [
                "Jane Doe",
                "PROFESSIONAL SUMMARY",
                "Systems administrator with 8 years supporting Windows Server, VMware, Azure, and PowerShell.",
                "TECHNICAL SKILLS",
                "PowerShell, Active Directory, VMware, Azure, Intune, Python",
                "EXPERIENCE",
                "Senior Systems Administrator | Example Corp | Jan 2021 - Present",
                "- Led Azure and Intune modernization efforts across 4 business units.",
                "Systems Administrator at Contoso | Mar 2017 - Dec 2020",
                "- Maintained Active Directory, VMware, and Windows Server infrastructure.",
                "EDUCATION AND CERTIFICATIONS",
                "CompTIA Security+",
                "AWS Certified Solutions Architect",
                "Active TS/SCI clearance with CI Polygraph",
            ]
        ),
        encoding="utf-8",
    )

    resume = ResumeParser().parse(resume_path)

    assert resume.experience_years >= 8.0
    assert "PowerShell" in resume.skills
    assert "Azure" in resume.skills
    assert "Security+" in resume.certifications
    assert "AWS Solutions Architect" in resume.certifications
    assert "TS/SCI" in resume.clearance_terms
    assert "CI Polygraph" in resume.clearance_terms
    assert any("Systems Administrator" in title for title in resume.recent_titles)
    assert "Recent titles:" in resume.summary_text
    assert "Certifications:" in resume.summary_text
    assert "Clearance:" in resume.summary_text


def test_resume_parser_merges_optional_llm_enrichment(tmp_path: Path) -> None:
    class FakeEnricher:
        def enrich_resume(self, *, raw_text, sections, extracted):  # noqa: ANN001
            assert "Systems administrator" in raw_text
            assert sections["experience"]
            assert extracted["skills"]
            return {
                "summary": "Windows infrastructure engineer with strong endpoint management experience.",
                "skills": ["Windows Server", "Intune"],
                "tools": ["VMware"],
                "certifications": ["Azure Administrator"],
                "clearance_terms": ["Secret"],
                "recent_titles": ["Infrastructure Engineer"],
                "experience_years_hint": 9,
            }

    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(
        "\n".join(
            [
                "Jane Doe",
                "SUMMARY",
                "Systems administrator supporting Windows and Azure environments.",
                "EXPERIENCE",
                "Systems Administrator | Jan 2018 - Present",
                "Managed VMware, Azure, and PowerShell automation.",
            ]
        ),
        encoding="utf-8",
    )

    resume = ResumeParser().parse(resume_path, llm_enricher=FakeEnricher())

    assert "Azure Administrator" in resume.certifications
    assert "Secret" in resume.clearance_terms
    assert "Infrastructure Engineer" in resume.recent_titles
    assert resume.experience_years >= 9.0
    assert "Structured profile:" in resume.summary_text


def test_resume_parser_builds_editable_application_profile(tmp_path: Path) -> None:
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(
        "\n".join(
            [
                "Jane Doe",
                "jane@example.com",
                "(555) 123-4567",
                "Denver, CO",
                "linkedin.com/in/janedoe",
                "SUMMARY",
                "Infrastructure engineer focused on Azure, VMware, PowerShell, and endpoint automation.",
                "EXPERIENCE",
                "Senior Infrastructure Engineer | Example Corp | Jan 2021 - Present",
                "- Led Azure modernization and Intune rollout.",
                "Systems Administrator at Contoso | Mar 2017 - Dec 2020",
                "- Maintained VMware, Windows Server, and PowerShell automation.",
                "EDUCATION",
                "Bachelor of Science in Information Technology",
                "State University",
            ]
        ),
        encoding="utf-8",
    )

    resume = ResumeParser().parse(resume_path)
    profile = resume.application_profile

    assert profile["basics"]["full_name"] == "Jane Doe"
    assert profile["basics"]["email"] == "jane@example.com"
    assert profile["basics"]["phone"]
    assert profile["work_history"]
    assert profile["work_history"][0]["title"]
    assert profile["skills"]
    assert profile["education"]


def test_engine_can_update_active_resume_profile(tmp_path: Path) -> None:
    storage = Storage("sqlite+pysqlite:///:memory:")
    engine = JobMatchEngine(storage=storage)
    resume_path = tmp_path / "resume.txt"
    resume_path.write_text(
        "\n".join(
            [
                "Jane Doe",
                "SUMMARY",
                "Systems administrator supporting Windows and Azure environments.",
                "EXPERIENCE",
                "Systems Administrator | Jan 2018 - Present",
                "Managed VMware, Azure, and PowerShell automation.",
            ]
        ),
        encoding="utf-8",
    )
    stored = engine.save_resume(resume_path)
    updated = engine.update_active_resume_profile(
        {
            "basics": {
                "full_name": "Jane Doe",
                "headline": "Senior Infrastructure Engineer",
                "summary": "Hands-on infrastructure engineer with strong automation experience.",
                "years_experience": 11,
            },
            "work_history": [
                {
                    "title": "Senior Infrastructure Engineer",
                    "company": "Example Corp",
                    "location": "Remote",
                    "start_date": "2021-01-01",
                    "end_date": "",
                    "is_current": True,
                    "description": "Led Azure modernization and PowerShell automation.",
                }
            ],
            "education": [],
            "skills": ["PowerShell", "Azure", "VMware"],
            "tools": ["Intune"],
            "certifications": ["Security+"],
            "clearance_terms": ["Secret"],
            "experience_years": 11,
        }
    )

    assert stored.id is not None
    assert updated.application_profile["basics"]["headline"] == "Senior Infrastructure Engineer"
    assert updated.experience_years == 11
    assert "PowerShell" in updated.skills
    assert "Secret" in updated.clearance_terms
    assert "Senior Infrastructure Engineer" in updated.summary_text
