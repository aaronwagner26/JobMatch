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
