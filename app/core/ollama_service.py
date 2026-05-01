from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from app.utils.text import normalize_whitespace, unique_sorted

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class OllamaStatus:
    running: bool
    model_ready: bool
    models: list[str]
    error: str = ""

    @property
    def available(self) -> bool:
        return self.running and self.model_ready


class OllamaEnricher:
    def __init__(
        self,
        *,
        base_url: str,
        model_name: str,
        resume_enabled: bool = True,
        job_enabled: bool = True,
        max_job_enrichments: int = 20,
        timeout_seconds: float = 25.0,
    ) -> None:
        self.base_url = normalize_whitespace(base_url).rstrip("/")
        self.model_name = normalize_whitespace(model_name)
        self.resume_enabled = resume_enabled
        self.job_enabled = job_enabled
        self.max_job_enrichments = max(0, int(max_job_enrichments))
        self.timeout_seconds = timeout_seconds
        self._status: OllamaStatus | None = None
        self._job_enrichments_used = 0

    def status(self, *, force_refresh: bool = False) -> OllamaStatus:
        if self._status is not None and not force_refresh:
            return self._status
        tags_url = f"{self.base_url}/api/tags"
        try:
            response = httpx.get(tags_url, timeout=self.timeout_seconds)
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:
            self._status = OllamaStatus(running=False, model_ready=False, models=[], error=str(exc))
            return self._status

        models = [
            normalize_whitespace(str(item.get("name") or ""))
            for item in payload.get("models", [])
            if isinstance(item, dict) and normalize_whitespace(str(item.get("name") or ""))
        ]
        folded = {model.casefold() for model in models}
        model_ready = self.model_name.casefold() in folded if self.model_name else False
        self._status = OllamaStatus(running=True, model_ready=model_ready, models=models)
        return self._status

    def enrich_resume(
        self,
        *,
        raw_text: str,
        sections: dict[str, str],
        extracted: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.resume_enabled or not self._ready():
            return {}
        prompt = self._resume_prompt(raw_text=raw_text, sections=sections, extracted=extracted)
        payload = self._generate_json(prompt)
        if not payload:
            return {}
        return {
            "summary": normalize_whitespace(payload.get("summary")),
            "skills": self._normalized_list(payload.get("skills")),
            "tools": self._normalized_list(payload.get("tools")),
            "certifications": self._normalized_list(payload.get("certifications")),
            "clearance_terms": self._normalized_list(payload.get("clearance_terms")),
            "recent_titles": self._normalized_list(payload.get("recent_titles")),
            "experience_years_hint": self._safe_float(payload.get("experience_years_hint")),
        }

    def enrich_job(
        self,
        *,
        title: str,
        company: str,
        location: str,
        description: str,
        extracted: dict[str, Any],
    ) -> dict[str, Any]:
        if not self.job_enabled or not self._ready():
            return {}
        if self.max_job_enrichments and self._job_enrichments_used >= self.max_job_enrichments:
            return {}
        if len(normalize_whitespace(description)) < 120:
            return {}
        self._job_enrichments_used += 1
        prompt = self._job_prompt(
            title=title,
            company=company,
            location=location,
            description=description,
            extracted=extracted,
        )
        payload = self._generate_json(prompt)
        if not payload:
            return {}
        return {
            "required_skills": self._normalized_list(payload.get("required_skills")),
            "preferred_skills": self._normalized_list(payload.get("preferred_skills")),
            "skills": self._normalized_list(payload.get("skills")),
            "clearance_terms": self._normalized_list(payload.get("clearance_terms")),
            "salary_text": normalize_whitespace(payload.get("salary_text")),
            "job_type": normalize_whitespace(payload.get("job_type")),
            "remote_mode": normalize_whitespace(payload.get("remote_mode")),
            "experience_years_hint": self._safe_float(payload.get("experience_years_hint")),
            "short_summary": normalize_whitespace(payload.get("short_summary")),
        }

    def _ready(self) -> bool:
        status = self.status()
        return status.available

    @property
    def job_enrichments_used(self) -> int:
        return self._job_enrichments_used

    def _generate_json(self, prompt: str) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self.base_url}/api/generate",
                json={
                    "model": self.model_name,
                    "prompt": prompt,
                    "stream": False,
                    "format": "json",
                    "options": {
                        "temperature": 0,
                    },
                },
                timeout=self.timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
            body = payload.get("response")
            if isinstance(body, dict):
                return body
            if isinstance(body, str):
                return self._parse_json_text(body)
        except Exception:
            logger.debug("Ollama enrichment call failed.", exc_info=True)
        return {}

    @staticmethod
    def _parse_json_text(text: str) -> dict[str, Any]:
        normalized = text.strip()
        if not normalized:
            return {}
        try:
            parsed = json.loads(normalized)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            start = normalized.find("{")
            end = normalized.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return {}
            try:
                parsed = json.loads(normalized[start : end + 1])
            except json.JSONDecodeError:
                return {}
            return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _normalized_list(value: Any) -> list[str]:
        if isinstance(value, str):
            items = [part.strip() for part in value.split(",")]
        elif isinstance(value, list):
            items = [normalize_whitespace(str(item)) for item in value]
        else:
            items = []
        return unique_sorted([item for item in items if normalize_whitespace(item)])

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        if value in (None, "", False):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _resume_prompt(self, *, raw_text: str, sections: dict[str, str], extracted: dict[str, Any]) -> str:
        resume_text = raw_text[:12000]
        return (
            "You extract structured resume data for a local job matcher.\n"
            "Use only facts explicitly supported by the resume text. Do not invent missing skills, certifications, titles, or clearances.\n"
            "Return one JSON object with exactly these keys:\n"
            "summary, skills, tools, certifications, clearance_terms, recent_titles, experience_years_hint.\n"
            "skills/tools/certifications/clearance_terms/recent_titles must be arrays of strings. experience_years_hint must be a number or null.\n"
            "Keep summary short and factual.\n\n"
            f"Current deterministic extraction:\n{json.dumps(extracted, ensure_ascii=True)}\n\n"
            f"Sections:\n{json.dumps(sections, ensure_ascii=True)}\n\n"
            f"Resume text:\n{resume_text}"
        )

    def _job_prompt(
        self,
        *,
        title: str,
        company: str,
        location: str,
        description: str,
        extracted: dict[str, Any],
    ) -> str:
        job_text = normalize_whitespace(description)[:10000]
        return (
            "You extract structured job requirement data for a local resume matcher.\n"
            "Use only explicit facts from the posting. Do not infer or embellish.\n"
            "Return one JSON object with exactly these keys:\n"
            "required_skills, preferred_skills, skills, clearance_terms, salary_text, job_type, remote_mode, experience_years_hint, short_summary.\n"
            "Array fields must be arrays of strings. experience_years_hint must be a number or null. salary_text/job_type/remote_mode/short_summary must be strings or empty strings.\n"
            "Only put skills into required_skills or preferred_skills if the posting clearly distinguishes them.\n"
            "Only return clearance_terms if the posting explicitly discusses an actual clearance requirement or eligibility.\n\n"
            f"Current deterministic extraction:\n{json.dumps(extracted, ensure_ascii=True)}\n\n"
            f"Title: {title}\nCompany: {company}\nLocation: {location}\n\n"
            f"Description:\n{job_text}"
        )
