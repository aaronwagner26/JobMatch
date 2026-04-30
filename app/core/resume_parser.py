from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from pathlib import Path

import fitz
from dateutil import parser as date_parser
from docx import Document

from app.core.types import ResumeProfile
from app.utils.skills import extract_certifications, extract_clearance_info, extract_skills, extract_tools
from app.utils.text import normalize_whitespace, text_hash

logger = logging.getLogger(__name__)

SECTION_HEADERS = {
    "summary": {"summary", "professional summary", "profile", "about", "objective"},
    "experience": {
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "employment history",
        "career history",
    },
    "skills": {"skills", "technical skills", "core competencies", "technologies", "technical proficiencies"},
    "education": {"education", "certifications", "education and certifications"},
    "projects": {"projects", "selected projects"},
}
MONTH_PATTERN = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
DATE_RANGE_RE = re.compile(
    rf"(?P<start>(?:{MONTH_PATTERN}\s+)?\d{{4}}|\d{{1,2}}\/\d{{4}})\s*(?:-|–|—|to)\s*"
    rf"(?P<end>present|current|now|(?:{MONTH_PATTERN}\s+)?\d{{4}}|\d{{1,2}}\/\d{{4}})",
    flags=re.IGNORECASE,
)
EXPLICIT_YEARS_RE = re.compile(r"(?P<years>\d+(?:\.\d+)?)\+?\s+years?", re.IGNORECASE)
TITLE_KEYWORDS = {
    "administrator",
    "analyst",
    "architect",
    "consultant",
    "coordinator",
    "developer",
    "director",
    "engineer",
    "lead",
    "manager",
    "operator",
    "owner",
    "scientist",
    "specialist",
    "support",
    "technician",
}
COMPANY_HINTS = {"inc", "llc", "corp", "corporation", "ltd", "university", "systems", "solutions", "company"}
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
PHONE_RE = re.compile(r"(?:\+?1[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]?\d{4}")
URL_RE = re.compile(r"(https?://[^\s]+|www\.[^\s]+)", re.IGNORECASE)
LINKEDIN_RE = re.compile(r"(https?://(?:www\.)?linkedin\.com/[^\s]+|linkedin\.com/[^\s]+)", re.IGNORECASE)
DEGREE_KEYWORDS = ("bachelor", "master", "associate", "phd", "doctor", "certificate", "certification", "b.s", "b.a", "m.s", "m.a")


class ResumeParser:
    def parse(self, file_path: str | Path, *, llm_enricher=None) -> ResumeProfile:
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

        cleaned_lines = self._clean_lines(raw_text)
        cleaned = "\n".join(cleaned_lines)
        if len(normalize_whitespace(cleaned)) < 40:
            raise ValueError("Resume text appears empty or unreadable after extraction.")

        sections = self._extract_sections(cleaned_lines)
        experience_text = sections.get("experience", cleaned)
        skills_text = "\n".join(
            filter(
                None,
                [
                    sections.get("skills", ""),
                    sections.get("summary", ""),
                    experience_text,
                    sections.get("projects", ""),
                ],
            )
        )
        skills = extract_skills(skills_text)
        tools = extract_tools(skills_text)
        certifications = extract_certifications("\n".join([sections.get("education", ""), sections.get("skills", ""), cleaned]))
        clearance_info = extract_clearance_info("\n".join([experience_text, sections.get("summary", ""), cleaned]))
        recent_titles = self._extract_recent_titles(experience_text)
        experience_spans, experience_years = self._estimate_experience(experience_text or cleaned)
        llm_summary = ""
        if llm_enricher is not None:
            enrichment = llm_enricher.enrich_resume(
                raw_text=cleaned,
                sections=sections,
                extracted={
                    "skills": skills,
                    "tools": tools,
                    "certifications": certifications,
                    "clearance_terms": list(clearance_info["terms"]),
                    "recent_titles": recent_titles,
                    "experience_years": experience_years,
                },
            )
            skills = self._merge_lists(skills, enrichment.get("skills"))
            tools = self._merge_lists(tools, enrichment.get("tools"))
            certifications = self._merge_lists(certifications, enrichment.get("certifications"))
            merged_clearance = self._merge_lists(list(clearance_info["terms"]), enrichment.get("clearance_terms"))
            clearance_info["terms"] = merged_clearance
            recent_titles = self._merge_lists(recent_titles, enrichment.get("recent_titles"))[:8]
            if enrichment.get("experience_years_hint"):
                experience_years = max(experience_years, float(enrichment["experience_years_hint"]))
            llm_summary = normalize_whitespace(str(enrichment.get("summary") or ""))
        application_profile = self._build_application_profile(
            lines=cleaned_lines,
            sections=sections,
            skills=skills,
            tools=tools,
            certifications=certifications,
            clearance_terms=list(clearance_info["terms"]),
            recent_titles=recent_titles,
            experience_years=experience_years,
            experience_spans=experience_spans,
        )
        summary_text = self._build_summary(
            sections=sections,
            skills=skills,
            tools=tools,
            certifications=certifications,
            clearance_terms=list(clearance_info["terms"]),
            recent_titles=recent_titles,
            experience_years=experience_years,
            application_profile=application_profile,
            llm_summary=llm_summary,
        )

        return ResumeProfile(
            id=None,
            filename=path.name,
            file_path=str(path),
            file_hash=text_hash(cleaned),
            raw_text=cleaned,
            summary_text=summary_text,
            skills=skills,
            tools=tools,
            certifications=certifications,
            clearance_terms=list(clearance_info["terms"]),
            recent_titles=recent_titles,
            experience_years=experience_years,
            experience_spans=experience_spans,
            sections=sections,
            application_profile=application_profile,
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

    def _clean_lines(self, raw_text: str) -> list[str]:
        lines: list[str] = []
        for line in raw_text.splitlines():
            stripped = normalize_whitespace(line.replace("\x0c", " "))
            if not stripped:
                continue
            if re.fullmatch(r"page \d+( of \d+)?", stripped, flags=re.IGNORECASE):
                continue
            lines.append(stripped)
        return lines

    def _extract_sections(self, lines: list[str]) -> dict[str, str]:
        sections: dict[str, list[str]] = {}
        current = "summary"
        sections.setdefault(current, [])
        for line in lines:
            header = self._match_section_header(line)
            if header is not None:
                current = header
                sections.setdefault(current, [])
                continue
            sections.setdefault(current, []).append(line)
        return {name: "\n".join(values).strip() for name, values in sections.items() if values}

    def _match_section_header(self, line: str) -> str | None:
        normalized = normalize_whitespace(line).strip(":").casefold()
        compact = re.sub(r"[^a-z ]+", "", normalized).strip()
        for name, options in SECTION_HEADERS.items():
            if compact in options:
                return name
        if len(compact.split()) <= 4 and (line.isupper() or line.istitle()):
            for name, options in SECTION_HEADERS.items():
                if compact in options:
                    return name
        return None

    def _extract_recent_titles(self, experience_text: str) -> list[str]:
        candidates: list[str] = []
        lines = [normalize_whitespace(line) for line in experience_text.splitlines() if normalize_whitespace(line)]
        for line in lines:
            date_match = DATE_RANGE_RE.search(line)
            if date_match:
                prefix = normalize_whitespace(line[: date_match.start()])
                if prefix:
                    candidates.append(self._clean_title_fragment(prefix))
                continue
            if line.startswith(("-", "*", "\u2022")):
                continue
            if "@" in line or "http" in line:
                continue
            if len(line) > 90:
                continue
            lowered = line.casefold()
            if not any(keyword in lowered for keyword in TITLE_KEYWORDS):
                continue
            if any(hint in lowered for hint in COMPANY_HINTS) and "|" not in line and " at " not in lowered:
                continue
            candidates.append(self._clean_title_fragment(line))
        unique_titles: list[str] = []
        seen: set[str] = set()
        for title in candidates:
            folded = title.casefold()
            if len(title) < 3 or folded in seen:
                continue
            seen.add(folded)
            unique_titles.append(title)
            if len(unique_titles) >= 8:
                break
        return unique_titles

    @staticmethod
    def _clean_title_fragment(value: str) -> str:
        fragment = value.split("|", 1)[0]
        fragment = re.split(r"\bat\b", fragment, maxsplit=1, flags=re.IGNORECASE)[0]
        fragment = re.split(r"\s{2,}", fragment, maxsplit=1)[0]
        return normalize_whitespace(fragment.strip(",-| "))

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
            serialized.append({"start": start.date().isoformat(), "end": end.date().isoformat(), "label": label})

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
        *,
        sections: dict[str, str],
        skills: list[str],
        tools: list[str],
        certifications: list[str],
        clearance_terms: list[str],
        recent_titles: list[str],
        experience_years: float,
        application_profile: dict[str, object] | None = None,
        llm_summary: str = "",
    ) -> str:
        parts: list[str] = []
        if llm_summary:
            parts.append(f"Structured profile: {llm_summary}")
        basics = dict((application_profile or {}).get("basics") or {})
        if basics.get("headline"):
            parts.append(f"Headline: {basics['headline']}")
        if recent_titles:
            parts.append(f"Recent titles: {', '.join(recent_titles[:6])}")
        if skills:
            parts.append(f"Skills: {', '.join(skills[:30])}")
        if tools:
            parts.append(f"Tools and platforms: {', '.join(tools[:22])}")
        if certifications:
            parts.append(f"Certifications: {', '.join(certifications[:12])}")
        if clearance_terms:
            parts.append(f"Clearance: {', '.join(clearance_terms)}")
        if experience_years:
            parts.append(f"Estimated experience: {experience_years:.1f} years")
        if sections.get("summary"):
            parts.append(f"Profile: {sections['summary']}")
        if sections.get("experience"):
            highlights = ResumeParser._experience_highlights(sections["experience"])
            if highlights:
                parts.append(f"Experience highlights: {highlights}")
        if sections.get("projects"):
            parts.append(f"Projects: {sections['projects']}")
        return "\n".join(parts)

    def _build_application_profile(
        self,
        *,
        lines: list[str],
        sections: dict[str, str],
        skills: list[str],
        tools: list[str],
        certifications: list[str],
        clearance_terms: list[str],
        recent_titles: list[str],
        experience_years: float,
        experience_spans: list[dict[str, str]],
    ) -> dict[str, object]:
        basics = self._extract_basics(lines, sections, recent_titles, experience_years)
        work_history = self._extract_work_history(sections.get("experience", ""), experience_spans)
        education = self._extract_education(sections.get("education", ""))
        return {
            "basics": basics,
            "work_history": work_history,
            "education": education,
            "skills": list(skills),
            "tools": list(tools),
            "certifications": list(certifications),
            "clearance_terms": list(clearance_terms),
            "recent_titles": list(recent_titles),
            "experience_years": float(experience_years or 0.0),
        }

    def _extract_basics(
        self,
        lines: list[str],
        sections: dict[str, str],
        recent_titles: list[str],
        experience_years: float,
    ) -> dict[str, object]:
        top_block = lines[:10]
        joined = "\n".join(lines[:20])
        full_name = ""
        for line in top_block:
            if EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line):
                continue
            if len(line.split()) > 5:
                continue
            if self._match_section_header(line) is not None:
                continue
            if any(char.isdigit() for char in line):
                continue
            full_name = line
            break
        email = normalize_whitespace(EMAIL_RE.search(joined).group(0)) if EMAIL_RE.search(joined) else ""
        phone = normalize_whitespace(PHONE_RE.search(joined).group(0)) if PHONE_RE.search(joined) else ""
        linkedin = normalize_whitespace(LINKEDIN_RE.search(joined).group(0)) if LINKEDIN_RE.search(joined) else ""
        urls = [normalize_whitespace(match.group(0)) for match in URL_RE.finditer(joined)]
        website = next((url for url in urls if "linkedin.com" not in url.casefold()), "")
        location = ""
        for line in top_block:
            if line in {full_name, email, phone, linkedin, website}:
                continue
            if EMAIL_RE.search(line) or PHONE_RE.search(line) or URL_RE.search(line):
                continue
            if "," in line and len(line) <= 80:
                location = line
                break
        headline = recent_titles[0] if recent_titles else ""
        summary = normalize_whitespace(sections.get("summary", "")) or ""
        return {
            "full_name": full_name,
            "email": email,
            "phone": phone,
            "location": location,
            "linkedin_url": linkedin,
            "website_url": website,
            "headline": headline,
            "summary": summary,
            "years_experience": float(experience_years or 0.0),
        }

    def _extract_work_history(self, experience_text: str, experience_spans: list[dict[str, str]]) -> list[dict[str, object]]:
        if not experience_text:
            return []
        lines = [normalize_whitespace(line) for line in experience_text.splitlines() if normalize_whitespace(line)]
        entries: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        last_non_bullet = ""
        for index, line in enumerate(lines):
            date_match = DATE_RANGE_RE.search(line)
            if date_match:
                if current:
                    entries.append(current)
                prefix = self._clean_title_fragment(line[: date_match.start()])
                title, company = self._split_title_company(prefix)
                if not title and index > 0:
                    title, company = self._split_title_company(last_non_bullet)
                current = {
                    "title": title,
                    "company": company,
                    "location": "",
                    "start_date": self._date_text_to_iso(date_match.group("start")),
                    "end_date": self._date_text_to_iso(date_match.group("end"), default_present=True),
                    "is_current": date_match.group("end").strip().casefold() in {"present", "current", "now"},
                    "description": "",
                }
                continue
            if line.startswith(("-", "*", "\u2022")):
                if current:
                    bullet = line.lstrip("-*\u2022 ").strip()
                    current["description"] = normalize_whitespace(
                        f"{current.get('description', '')}\n{bullet}".strip()
                    )
                continue
            last_non_bullet = line
            if current and not current.get("company"):
                title, company = self._split_title_company(line)
                if company:
                    if not current.get("title"):
                        current["title"] = title
                    current["company"] = company
                    continue
            if current and not current.get("location") and "," in line and len(line) <= 90:
                current["location"] = line
        if current:
            entries.append(current)

        deduped: list[dict[str, object]] = []
        seen: set[tuple[str, str, str]] = set()
        for entry in entries:
            key = (
                normalize_whitespace(str(entry.get("title") or "")).casefold(),
                normalize_whitespace(str(entry.get("company") or "")).casefold(),
                normalize_whitespace(str(entry.get("start_date") or "")).casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            if not entry.get("title") and not entry.get("company"):
                continue
            deduped.append(entry)
        if deduped:
            return deduped[:8]

        fallback: list[dict[str, object]] = []
        for span, title in zip(experience_spans[:4], recent_titles := self._extract_recent_titles(experience_text)[:4], strict=False):
            fallback.append(
                {
                    "title": title,
                    "company": "",
                    "location": "",
                    "start_date": span.get("start", ""),
                    "end_date": span.get("end", ""),
                    "is_current": span.get("label", "").casefold().endswith("present"),
                    "description": "",
                }
            )
        return fallback

    def _extract_education(self, education_text: str) -> list[dict[str, object]]:
        if not education_text:
            return []
        lines = [normalize_whitespace(line) for line in education_text.splitlines() if normalize_whitespace(line)]
        entries: list[dict[str, object]] = []
        current: dict[str, object] | None = None
        for line in lines:
            date_match = DATE_RANGE_RE.search(line)
            looks_like_degree = any(keyword in line.casefold() for keyword in DEGREE_KEYWORDS)
            if looks_like_degree or date_match:
                if current:
                    entries.append(current)
                degree_text = self._clean_title_fragment(line[: date_match.start()]) if date_match else line
                current = {
                    "school": "",
                    "degree": degree_text if looks_like_degree else "",
                    "field_of_study": "",
                    "start_date": self._date_text_to_iso(date_match.group("start")) if date_match else "",
                    "end_date": self._date_text_to_iso(date_match.group("end"), default_present=True) if date_match else "",
                    "description": "",
                }
                continue
            if current and not current.get("school"):
                current["school"] = line
            elif current:
                current["description"] = normalize_whitespace(f"{current.get('description', '')}\n{line}".strip())
        if current:
            entries.append(current)
        if entries:
            return entries[:6]
        return [{"school": line, "degree": "", "field_of_study": "", "start_date": "", "end_date": "", "description": ""} for line in lines[:4]]

    @staticmethod
    def _split_title_company(value: str) -> tuple[str, str]:
        text = normalize_whitespace(value)
        if not text:
            return "", ""
        if "|" in text:
            left, right = [normalize_whitespace(part) for part in text.split("|", 1)]
            return left, right
        if " at " in text.casefold():
            parts = re.split(r"\bat\b", text, maxsplit=1, flags=re.IGNORECASE)
            return normalize_whitespace(parts[0]), normalize_whitespace(parts[1]) if len(parts) > 1 else ""
        return text, ""

    @staticmethod
    def _date_text_to_iso(value: str, *, default_present: bool = False) -> str:
        parsed = ResumeParser._parse_partial_date(value, default_present=default_present)
        return parsed.date().isoformat() if parsed else ""

    @staticmethod
    def _merge_lists(left: list[str], right: list[str] | None) -> list[str]:
        values = list(left)
        if right:
            values.extend(str(item) for item in right)
        seen: dict[str, str] = {}
        for value in values:
            normalized = normalize_whitespace(value)
            if normalized:
                seen.setdefault(normalized.casefold(), normalized)
        return list(seen.values())

    @staticmethod
    def _experience_highlights(experience_text: str, limit: int = 4) -> str:
        lines = [normalize_whitespace(line) for line in experience_text.splitlines() if normalize_whitespace(line)]
        highlights: list[str] = []
        for line in lines:
            lowered = line.casefold()
            if line.startswith(("-", "*", "\u2022")) or any(token in lowered for token in TITLE_KEYWORDS):
                highlights.append(line.lstrip("-*\u2022 ").strip())
            if len(highlights) >= limit:
                break
        return " | ".join(highlights)
