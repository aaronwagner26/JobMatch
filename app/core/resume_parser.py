from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import fitz
from dateutil import parser as date_parser
from docx import Document

from app.core.types import ResumeProfile
from app.utils.skills import extract_skills, extract_tools
from app.utils.text import normalize_whitespace, text_hash

logger = logging.getLogger(__name__)

SECTION_HEADERS = {
    "summary": {"summary", "professional summary", "profile", "about"},
    "experience": {"experience", "work experience", "professional experience", "employment"},
    "skills": {"skills", "technical skills", "core competencies", "technologies"},
    "education": {"education", "certifications"},
    "projects": {"projects", "selected projects"},
}
MONTH_PATTERN = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>(?:{MONTH_PATTERN}\s+)?\d{{4}}|\d{{1,2}}\/\d{{4}})\s*(?:-|–|—|to)\s*"
    rf"(?P<end>present|current|now|(?:{MONTH_PATTERN}\s+)?\d{{4}}|\d{{1,2}}\/\d{{4}})",
    flags=re.IGNORECASE,
)
EXPLICIT_YEARS_RE = re.compile(r"(?P<years>\d+(?:\.\d+)?)\+?\s+years?", re.IGNORECASE)


class ResumeParser:
    def parse(self, file_path: str | Path) -> ResumeProfile:
        path = Path(file_path)
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            raw_text = self._extract_pdf_text(path)
        elif suffix == ".docx":
            raw_text = self._extract_docx_text(path)
        elif suffix in {".txt", ".md"}:
            raw_text = path.read_text(encoding="utf-8", errors="ignore")
        else:
            raise ValueError("Unsupported resume format. Please upload a PDF or DOCX file.")

        cleaned = normalize_whitespace(raw_text)
        if len(cleaned) < 40:
            raise ValueError("Resume text appears empty or unreadable after extraction.")

        sections = self._extract_sections(raw_text)
        skill_text = "\n".join([sections.get("skills", ""), sections.get("experience", ""), cleaned])
        skills = extract_skills(skill_text)
        tools = extract_tools(skill_text)
        experience_spans, experience_years = self._estimate_experience(raw_text)
        summary_text = self._build_summary(cleaned, sections, skills, tools, experience_years)

        return ResumeProfile(
            id=None,
            filename=path.name,
            file_path=str(path),
            file_hash=text_hash(cleaned),
            raw_text=cleaned,
            summary_text=summary_text,
            skills=skills,
            tools=tools,
            experience_years=experience_years,
            experience_spans=experience_spans,
            sections=sections,
        )

    @staticmethod
    def _extract_pdf_text(path: Path) -> str:
        logger.info("Extracting PDF resume text from %s", path)
        with fitz.open(path) as document:
            pages = [page.get_text("text") for page in document]
        return "\n".join(pages)

    @staticmethod
    def _extract_docx_text(path: Path) -> str:
        logger.info("Extracting DOCX resume text from %s", path)
        document = Document(path)
        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
        return "\n".join(paragraphs)

    def _extract_sections(self, raw_text: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current = "summary"
        sections.setdefault(current, [])
        for line in raw_text.splitlines():
            stripped = normalize_whitespace(line)
            if not stripped:
                continue
            lower = stripped.casefold().rstrip(":")
            matched_header = next((name for name, options in SECTION_HEADERS.items() if lower in options), None)
            if matched_header:
                current = matched_header
                sections.setdefault(current, [])
                continue
            if stripped.isupper() and len(stripped.split()) <= 4:
                maybe_header = stripped.casefold()
                matched_header = next((name for name, options in SECTION_HEADERS.items() if maybe_header in options), None)
                if matched_header:
                    current = matched_header
                    sections.setdefault(current, [])
                    continue
            sections.setdefault(current, []).append(stripped)
        return {name: normalize_whitespace(" ".join(lines)) for name, lines in sections.items() if lines}

    def _estimate_experience(self, raw_text: str) -> tuple[list[dict[str, str]], float]:
        spans: list[tuple[datetime, datetime, str]] = []
        for match in DATE_RANGE_RE.finditer(raw_text):
            start = self._parse_partial_date(match.group("start"))
            end = self._parse_partial_date(match.group("end"), default_present=True)
            if start is None or end is None or end < start:
                continue
            spans.append((start, end, match.group(0)))

        month_keys: set[tuple[int, int]] = set()
        serialized: list[dict[str, str]] = []
        for start, end, label in spans:
            cursor = datetime(start.year, start.month, 1, tzinfo=UTC)
            limit = datetime(end.year, end.month, 1, tzinfo=UTC)
            while cursor <= limit:
                month_keys.add((cursor.year, cursor.month))
                if cursor.month == 12:
                    cursor = datetime(cursor.year + 1, 1, 1, tzinfo=UTC)
                else:
                    cursor = datetime(cursor.year, cursor.month + 1, 1, tzinfo=UTC)
            serialized.append(
                {
                    "start": start.date().isoformat(),
                    "end": end.date().isoformat(),
                    "label": label,
                }
            )

        if month_keys:
            experience_years = round(len(month_keys) / 12.0, 1)
            return serialized, experience_years

        explicit_years = [float(match.group("years")) for match in EXPLICIT_YEARS_RE.finditer(raw_text)]
        return serialized, max(explicit_years, default=0.0)

    @staticmethod
    def _parse_partial_date(value: str, *, default_present: bool = False) -> datetime | None:
        if not value:
            return None
        lower = value.casefold().strip()
        if lower in {"present", "current", "now"} and default_present:
            now = datetime.now(UTC)
            return datetime(now.year, now.month, 1, tzinfo=UTC)
        try:
            parsed = date_parser.parse(value, default=datetime(2000, 1, 1))
        except (TypeError, ValueError, OverflowError):
            return None
        return datetime(parsed.year, parsed.month, 1, tzinfo=UTC)

    @staticmethod
    def _build_summary(
        cleaned_text: str,
        sections: dict[str, str],
        skills: list[str],
        tools: list[str],
        experience_years: float,
    ) -> str:
        parts = []
        if skills:
            parts.append(f"Skills: {', '.join(skills[:24])}")
        if tools:
            parts.append(f"Tools: {', '.join(tools[:18])}")
        if experience_years:
            parts.append(f"Estimated experience: {experience_years:.1f} years")
        if sections.get("summary"):
            parts.append(f"Profile: {sections['summary']}")
        if sections.get("experience"):
            parts.append(f"Experience highlights: {sections['experience']}")
        if not parts:
            parts.append(cleaned_text[:2000])
        return "\n".join(parts)

