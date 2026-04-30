from __future__ import annotations

import re
from datetime import UTC, datetime

from app.core.types import JobSourceConfig, NormalizedJob
from app.utils.skills import (
    detect_job_type,
    detect_remote_mode,
    extract_clearance_info,
    extract_salary_info,
    extract_skills,
)
from app.utils.text import (
    canonical_job_url,
    clipped_excerpt,
    dt_to_iso,
    normalize_whitespace,
    parse_datetime,
    text_hash,
    unique_sorted,
)

REQUIREMENT_SPLIT_RE = re.compile(
    r"(requirements|qualifications|must have|what you[' ]?ll need|what we[' ]?re looking for)",
    re.IGNORECASE,
)
PREFERRED_SPLIT_RE = re.compile(
    r"(preferred|nice to have|bonus points|would be great|desired qualifications)",
    re.IGNORECASE,
)
YEARS_RE = re.compile(r"(\d+(?:\.\d+)?)\+?\s+years?", re.IGNORECASE)


class JobNormalizer:
    def normalize(self, source: JobSourceConfig, payload: dict, *, llm_enricher=None) -> NormalizedJob:
        title = normalize_whitespace(payload.get("title"))
        company = normalize_whitespace(payload.get("company") or source.name)
        location = normalize_whitespace(payload.get("location"))
        description = normalize_whitespace(payload.get("description") or payload.get("summary") or "")
        raw_salary_text = normalize_whitespace(str(payload.get("salary_text") or ""))
        raw_employment_text = normalize_whitespace(str(payload.get("employment_text") or ""))
        if not title:
            raise ValueError("Job payload is missing a title.")

        combined_text = "\n".join(filter(None, [title, company, location, description, payload.get("requirements_text", "")]))
        required_text = self._section_text(payload.get("requirements_text"), description, REQUIREMENT_SPLIT_RE)
        preferred_text = self._section_text(payload.get("preferred_text"), description, PREFERRED_SPLIT_RE)
        required_skills = extract_skills(required_text or combined_text)
        preferred_skills = extract_skills(preferred_text)
        all_skills = extract_skills(combined_text)
        remote_mode = payload.get("remote_mode") or detect_remote_mode(f"{location} {description}")
        job_type = payload.get("job_type") or detect_job_type(
            "\n".join(filter(None, [title, description, raw_employment_text, raw_salary_text]))
        )
        clearance_info = extract_clearance_info(combined_text)
        clearance_terms = list(clearance_info["terms"])
        salary_info = extract_salary_info(
            "\n".join(
                filter(
                    None,
                    [
                        str(payload.get("salary_text") or ""),
                        str(payload.get("summary") or ""),
                        description,
                        raw_employment_text,
                    ],
                )
            )
        )
        employment_text = raw_employment_text
        if raw_salary_text and not salary_info.get("display"):
            employment_text = self._merge_text_fragments(raw_employment_text, raw_salary_text)
        experience_years = self._extract_experience_years(required_text or description)
        llm_summary = ""
        if llm_enricher is not None:
            enrichment = llm_enricher.enrich_job(
                title=title,
                company=company,
                location=location,
                description=description or payload.get("summary") or "",
                extracted={
                    "required_skills": required_skills,
                    "preferred_skills": preferred_skills,
                    "skills": all_skills,
                    "clearance_terms": clearance_terms,
                    "salary_text": salary_info.get("display"),
                    "experience_years": experience_years,
                    "job_type": job_type,
                    "remote_mode": remote_mode,
                },
            )
            required_skills = self._merge_skill_lists(required_skills, enrichment.get("required_skills"))
            preferred_skills = [
                skill for skill in self._merge_skill_lists(preferred_skills, enrichment.get("preferred_skills")) if skill not in required_skills
            ]
            all_skills = self._merge_skill_lists(all_skills, enrichment.get("skills"))
            clearance_terms = self._merge_string_lists(clearance_terms, enrichment.get("clearance_terms"))
            if clearance_terms:
                clearance_info["summary"] = ", ".join(clearance_terms)
            if enrichment.get("salary_text") and not salary_info.get("display"):
                salary_info = extract_salary_info(str(enrichment.get("salary_text") or ""))
                if not salary_info.get("display"):
                    salary_info["display"] = normalize_whitespace(str(enrichment.get("salary_text") or ""))
            if enrichment.get("experience_years_hint"):
                experience_years = max(experience_years or 0.0, float(enrichment["experience_years_hint"]))
            if enrichment.get("job_type") and not job_type:
                job_type = normalize_whitespace(str(enrichment.get("job_type")))
            if enrichment.get("remote_mode") and remote_mode == "unknown":
                remote_mode = normalize_whitespace(str(enrichment.get("remote_mode")))
            llm_summary = normalize_whitespace(str(enrichment.get("short_summary") or ""))
        posted_at = parse_datetime(payload.get("posted_at"))
        external_id = self.derive_external_id(source, payload)
        listing_hash = payload.get("listing_hash") or self.build_listing_hash(source, payload)
        canonical_url = payload.get("canonical_url") or canonical_job_url(payload.get("url") or source.url)

        summary_text = "\n".join(
            filter(
                None,
                [
                    f"{title} at {company}",
                    f"Location: {location}" if location else "",
                    f"Remote mode: {remote_mode}",
                    f"Type: {job_type}" if job_type else "",
                    f"Salary: {salary_info['display']}" if salary_info.get("display") else "",
                    f"Clearance: {clearance_info['summary']}" if clearance_info.get("summary") else "",
                    f"Required skills: {', '.join(required_skills[:16])}" if required_skills else "",
                    f"Preferred skills: {', '.join(preferred_skills[:16])}" if preferred_skills else "",
                    f"LLM summary: {llm_summary}" if llm_summary else "",
                    clipped_excerpt(description, 900),
                ],
            )
        )

        metadata = dict(payload.get("metadata") or {})
        metadata.update(
            {
                "normalized_at": dt_to_iso(datetime.now(UTC)),
                "snippet": clipped_excerpt(description, 320),
                "listing_hash": listing_hash,
                "canonical_url": canonical_url,
                "clearance_summary": clearance_info.get("summary") or "",
                "salary_display": salary_info.get("display") or "",
                "raw_payload_keys": sorted(payload.keys()),
            }
        )

        return NormalizedJob(
            id=None,
            source_id=source.id or 0,
            source_name=source.name,
            source_type=source.source_type,
            external_id=external_id,
            title=title,
            company=company,
            location=location,
            remote_mode=remote_mode,
            job_type=job_type,
            clearance_terms=clearance_terms,
            salary_min=salary_info.get("minimum"),
            salary_max=salary_info.get("maximum"),
            salary_currency=salary_info.get("currency"),
            salary_interval=salary_info.get("interval"),
            salary_text=salary_info.get("display"),
            posted_at=posted_at,
            url=payload.get("url") or source.url,
            description=description,
            summary_text=summary_text,
            skills=all_skills,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            experience_years=experience_years,
            employment_text=employment_text,
            metadata=metadata,
            content_hash=text_hash(summary_text),
        )

    @staticmethod
    def derive_external_id(source: JobSourceConfig, payload: dict) -> str:
        title = normalize_whitespace(payload.get("title"))
        company = normalize_whitespace(payload.get("company") or source.name)
        url = payload.get("url") or source.url
        return str(payload.get("external_id") or payload.get("raw_id") or text_hash(f"{source.id}:{title}:{company}:{url}"))

    @staticmethod
    def build_listing_hash(source: JobSourceConfig, payload: dict) -> str:
        listing_signature = "\n".join(
            [
                normalize_whitespace(payload.get("title")),
                normalize_whitespace(payload.get("company") or source.name),
                normalize_whitespace(payload.get("location")),
                normalize_whitespace(payload.get("summary") or payload.get("description")),
                normalize_whitespace(str(payload.get("posted_at") or "")),
                canonical_job_url(payload.get("url") or source.url),
            ]
        )
        return text_hash(listing_signature)

    @staticmethod
    def _section_text(existing: str | None, description: str, splitter: re.Pattern[str]) -> str:
        if existing:
            return normalize_whitespace(existing)
        parts = splitter.split(description, maxsplit=1)
        if len(parts) >= 3:
            return normalize_whitespace(parts[2])
        return ""

    @staticmethod
    def _extract_experience_years(text: str) -> float | None:
        matches = [float(match.group(1)) for match in YEARS_RE.finditer(text or "")]
        if not matches:
            return None
        return max(matches)

    @staticmethod
    def _merge_skill_lists(left: list[str], right: list[str] | None) -> list[str]:
        values: list[str] = []
        values.extend(left)
        if right:
            values.extend(str(item) for item in right)
        merged = unique_sorted(values)
        return merged

    @staticmethod
    def _merge_string_lists(left: list[str], right: list[str] | None) -> list[str]:
        values: list[str] = []
        values.extend(left)
        if right:
            values.extend(str(item) for item in right)
        return unique_sorted(values)

    @staticmethod
    def _merge_text_fragments(*values: str) -> str:
        ordered: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized = normalize_whitespace(value)
            if not normalized:
                continue
            folded = normalized.casefold()
            if folded in seen:
                continue
            seen.add(folded)
            ordered.append(normalized)
        return " | ".join(ordered)
