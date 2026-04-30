from pathlib import Path

from app.core.resume_parser import ResumeParser


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
