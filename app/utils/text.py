from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import numpy as np
from bs4 import BeautifulSoup
from dateutil import parser as date_parser

WHITESPACE_RE = re.compile(r"\s+")
NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
TRACKING_QUERY_KEYS = {
    "cf-turnstile-response",
    "fbclid",
    "gclid",
    "gh_src",
    "lever-source",
    "ref",
    "referrer",
    "source",
    "src",
    "tracking",
    "trk",
    "vjk",
}
INDEED_ALLOWED_QUERY_KEYS = {
    "explvl",
    "filter",
    "fromage",
    "jt",
    "l",
    "q",
    "radius",
    "remotejob",
    "salaryType",
    "sc",
    "sort",
    "start",
}
INDEED_ALLOWED_QUERY_KEYS_FOLDED = {key.casefold() for key in INDEED_ALLOWED_QUERY_KEYS}


def normalize_whitespace(text: str | None) -> str:
    if not text:
        return ""
    return WHITESPACE_RE.sub(" ", text).strip()


def strip_html(markup: str | None) -> str:
    if not markup:
        return ""
    soup = BeautifulSoup(markup, "html.parser")
    return normalize_whitespace(soup.get_text(" "))


def safe_filename(value: str, suffix: str = "") -> str:
    stem = NON_ALNUM_RE.sub("-", value.lower()).strip("-") or "file"
    return f"{stem}{suffix}"


def text_hash(value: str | None) -> str:
    normalized = normalize_whitespace(value or "")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def unique_sorted(values: list[str]) -> list[str]:
    seen: dict[str, str] = {}
    for value in values:
        normalized = normalize_whitespace(value)
        if normalized:
            seen.setdefault(normalized.casefold(), normalized)
    return sorted(seen.values(), key=str.casefold)


def absolute_url(base_url: str | None, link: str | None) -> str:
    if not link:
        return ""
    return urljoin(base_url or "", link)


def canonical_job_url(url: str | None) -> str:
    if not url:
        return ""
    split = urlsplit(url)
    if not split.scheme or not split.netloc:
        return normalize_whitespace(url)
    filtered_query = [
        (key, value)
        for key, value in parse_qsl(split.query, keep_blank_values=False)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_QUERY_KEYS
    ]
    normalized_path = re.sub(r"/{2,}", "/", split.path or "/").rstrip("/") or "/"
    query = urlencode(sorted(filtered_query))
    return urlunsplit((split.scheme.casefold(), split.netloc.casefold(), normalized_path, query, ""))


def canonical_job_key(title: str, company: str, location: str, url: str | None, job_type: str | None = None) -> str:
    canonical_url = canonical_job_url(url)
    if canonical_url:
        return f"url:{canonical_url}"
    pieces = [
        normalize_whitespace(title).casefold(),
        normalize_whitespace(company).casefold(),
        normalize_whitespace(location).casefold(),
        normalize_whitespace(job_type).casefold(),
    ]
    return "title:" + "|".join(pieces)


def sanitize_source_url(url: str | None, source_type: str = "auto") -> str:
    if not url:
        return ""
    normalized = normalize_whitespace(url)
    split = urlsplit(normalized)
    if not split.scheme or not split.netloc:
        return normalized

    host = split.netloc.casefold()
    params = parse_qsl(split.query, keep_blank_values=False)
    filtered: list[tuple[str, str]] = []
    effective_source = source_type.casefold()
    is_indeed = effective_source == "indeed" or "indeed." in host

    for key, value in params:
        folded = key.casefold()
        if folded.startswith("utm_") or folded.startswith("__cf_"):
            continue
        if folded in TRACKING_QUERY_KEYS:
            continue
        if is_indeed:
            if folded == "from":
                continue
            if folded not in INDEED_ALLOWED_QUERY_KEYS_FOLDED:
                continue
        filtered.append((key, value))

    normalized_path = re.sub(r"/{2,}", "/", split.path or "/").rstrip("/") or "/"
    query = urlencode(sorted(filtered))
    return urlunsplit((split.scheme.casefold(), split.netloc.casefold(), normalized_path, query, ""))


def parse_datetime(value: Any) -> datetime | None:
    if value in (None, "", 0):
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, (int, float)):
        if value > 10_000_000_000:
            value = value / 1000
        return datetime.fromtimestamp(value, tz=UTC)
    try:
        parsed = date_parser.parse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def dt_to_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.astimezone(UTC).isoformat()


def iso_to_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return parse_datetime(value)
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def cosine_similarity(left: list[float] | np.ndarray, right: list[float] | np.ndarray) -> float:
    left_array = np.asarray(left, dtype=float)
    right_array = np.asarray(right, dtype=float)
    left_norm = np.linalg.norm(left_array)
    right_norm = np.linalg.norm(right_array)
    if left_norm == 0 or right_norm == 0:
        return 0.0
    similarity = float(np.dot(left_array, right_array) / (left_norm * right_norm))
    return max(0.0, min(similarity, 1.0))


def clipped_excerpt(text: str, limit: int = 240) -> str:
    normalized = normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip() + "…"


def write_text_file(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def decode_html(value: str | None) -> str:
    return html.unescape(value or "")
