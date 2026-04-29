from __future__ import annotations

import logging
import re
from collections import OrderedDict
from urllib.parse import parse_qsl, quote_plus, urlsplit

import httpx
from bs4 import BeautifulSoup

from app.core.types import DiscoveredSourceCandidate
from app.utils.config import DEFAULT_HEADERS, DEFAULT_HTTP_TIMEOUT
from app.utils.text import absolute_url, normalize_whitespace, sanitize_source_url

logger = logging.getLogger(__name__)

SEARCH_RESULT_LIMIT = 6
PAGE_FETCH_LIMIT = 4
CAREER_LINK_FETCH_LIMIT = 2
EXCLUDED_HOSTS = {
    "indeed.com",
    "linkedin.com",
    "glassdoor.com",
    "ziprecruiter.com",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
    "youtube.com",
    "wikipedia.org",
}


class SourceDiscovery:
    def discover(self, query: str) -> list[DiscoveredSourceCandidate]:
        normalized = normalize_whitespace(query)
        if not normalized:
            return []

        candidates: list[DiscoveredSourceCandidate] = []
        seen_pages: set[str] = set()

        if self._looks_like_url(normalized):
            seed_url = self._normalize_seed_url(normalized)
            candidates.extend(self._candidate_from_url(seed_url, reason="Direct URL"))
            candidates.extend(self._discover_from_page(seed_url, seen_pages, reason=f"Found on {seed_url}"))
        else:
            search_results = self._search_results(normalized)
            company_label = normalized
            for title, result_url in search_results[:SEARCH_RESULT_LIMIT]:
                candidates.extend(self._candidate_from_url(result_url, reason=f"Search result: {title}"))
                if self._host_is_excluded(result_url):
                    continue
                candidates.extend(self._discover_from_page(result_url, seen_pages, reason=f"Found on {result_url}", company_hint=company_label))

        deduped = self._dedupe_candidates(candidates)
        return deduped[:10]

    def _discover_from_page(
        self,
        url: str,
        seen_pages: set[str],
        *,
        reason: str,
        company_hint: str | None = None,
    ) -> list[DiscoveredSourceCandidate]:
        normalized_url = sanitize_source_url(url, "custom_url")
        if normalized_url in seen_pages:
            return []
        seen_pages.add(normalized_url)
        html = self._fetch_html(normalized_url)
        if not html:
            return []

        candidates: list[DiscoveredSourceCandidate] = []
        soup = BeautifulSoup(html, "html.parser")
        for anchor in soup.select("a[href]"):
            href = absolute_url(normalized_url, anchor.get("href"))
            if not href:
                continue
            candidates.extend(self._candidate_from_url(href, reason=reason, company_hint=company_hint))

        if candidates:
            return candidates

        career_links = self._career_links_from_page(soup, normalized_url)
        for career_url in career_links[:CAREER_LINK_FETCH_LIMIT]:
            if sanitize_source_url(career_url, "custom_url") in seen_pages:
                continue
            candidates.extend(
                self._discover_from_page(
                    career_url,
                    seen_pages,
                    reason=f"Found on {career_url}",
                    company_hint=company_hint,
                )
            )

        if candidates:
            return candidates

        if self._looks_like_careers_page(normalized_url):
            host_name = company_hint or self._host_label(normalized_url)
            candidates.append(
                DiscoveredSourceCandidate(
                    name=host_name,
                    source_type="custom_url",
                    url=normalized_url,
                    platform="careers page",
                    reason=f"Careers page: {normalized_url}",
                    use_playwright=self._should_default_playwright(normalized_url, "careers page"),
                )
            )
        return candidates

    def _search_results(self, query: str) -> list[tuple[str, str]]:
        search_queries = [f"{query} careers", f"{query} jobs"]
        results: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for term in search_queries:
            html = self._fetch_html(f"https://html.duckduckgo.com/html/?q={quote_plus(term)}")
            if not html:
                continue
            soup = BeautifulSoup(html, "html.parser")
            for anchor in soup.select("a.result__a"):
                href = self._unwrap_duckduckgo_url(anchor.get("href") or "")
                if not href or href in seen_urls:
                    continue
                seen_urls.add(href)
                results.append((normalize_whitespace(anchor.get_text(" ")), href))
        return results

    def _candidate_from_url(
        self,
        url: str,
        *,
        reason: str,
        company_hint: str | None = None,
    ) -> list[DiscoveredSourceCandidate]:
        normalized_url = sanitize_source_url(url, "custom_url")
        host = urlsplit(normalized_url).netloc.casefold()
        if not host:
            return []

        platform = None
        source_type = "custom_url"
        normalized_board_url = normalized_url
        identifier = None
        use_playwright = False

        if "greenhouse" in host:
            platform = "greenhouse"
            source_type = "greenhouse"
            normalized_board_url, identifier = self._normalize_greenhouse_board_url(normalized_url)
        elif "lever.co" in host:
            platform = "lever"
            source_type = "lever"
            normalized_board_url, identifier = self._normalize_lever_board_url(normalized_url)
        elif "ashbyhq.com" in host:
            platform = "ashby"
            normalized_board_url = self._normalize_first_segments(normalized_url, 1)
        elif "smartrecruiters.com" in host:
            platform = "smartrecruiters"
            normalized_board_url = self._normalize_first_segments(normalized_url, 1)
        elif "jobvite.com" in host:
            platform = "jobvite"
            normalized_board_url = self._normalize_first_segments(normalized_url, 2)
        elif "myworkdayjobs.com" in host or "workdayjobs.com" in host:
            platform = "workday"
            use_playwright = True
            normalized_board_url = self._normalize_first_segments(normalized_url, 2)
        elif "bamboohr.com" in host and "/careers" in urlsplit(normalized_url).path.casefold():
            platform = "bamboohr"
            normalized_board_url = self._normalize_first_segments(normalized_url, 1)
        elif self._looks_like_careers_page(normalized_url):
            platform = "careers page"
            use_playwright = self._should_default_playwright(normalized_url, platform)
        else:
            return []

        if platform is None:
            return []

        return [
            DiscoveredSourceCandidate(
                name=company_hint or self._host_label(normalized_board_url),
                source_type=source_type,
                url=normalized_board_url,
                platform=platform,
                reason=reason,
                identifier=identifier,
                use_playwright=use_playwright,
            )
        ]

    def _fetch_html(self, url: str) -> str | None:
        try:
            response = httpx.get(
                url,
                timeout=DEFAULT_HTTP_TIMEOUT,
                follow_redirects=True,
                headers=DEFAULT_HEADERS,
            )
            response.raise_for_status()
            return response.text
        except Exception as exc:
            logger.debug("Source discovery fetch failed for %s: %s", url, exc)
            return None

    @staticmethod
    def _unwrap_duckduckgo_url(url: str) -> str:
        if not url:
            return ""
        if url.startswith("//"):
            url = "https:" + url
        split = urlsplit(url)
        if "duckduckgo.com" not in split.netloc.casefold():
            return url
        params = dict(parse_qsl(split.query, keep_blank_values=False))
        target = params.get("uddg")
        return target or url

    @staticmethod
    def _normalize_seed_url(value: str) -> str:
        split = urlsplit(value)
        if split.scheme:
            return value
        return "https://" + value

    @staticmethod
    def _looks_like_url(value: str) -> bool:
        split = urlsplit(value)
        return bool(split.scheme and split.netloc) or ("." in value and " " not in value)

    @staticmethod
    def _normalize_greenhouse_board_url(url: str) -> tuple[str, str | None]:
        split = urlsplit(url)
        parts = [part for part in split.path.split("/") if part]
        if not parts:
            return sanitize_source_url(url, "greenhouse"), None
        slug = parts[0]
        if slug in {"boards", "job-boards", "embed", "jobapp"} and len(parts) > 1:
            slug = parts[1]
        board_url = f"{split.scheme}://{split.netloc}/{slug}"
        return sanitize_source_url(board_url, "greenhouse"), slug

    @staticmethod
    def _normalize_lever_board_url(url: str) -> tuple[str, str | None]:
        split = urlsplit(url)
        parts = [part for part in split.path.split("/") if part]
        if not parts:
            return sanitize_source_url(url, "lever"), None
        slug = parts[0]
        board_url = f"{split.scheme}://{split.netloc}/{slug}"
        return sanitize_source_url(board_url, "lever"), slug

    @staticmethod
    def _normalize_first_segments(url: str, count: int) -> str:
        split = urlsplit(url)
        parts = [part for part in split.path.split("/") if part][:count]
        path = "/" + "/".join(parts) if parts else "/"
        return sanitize_source_url(f"{split.scheme}://{split.netloc}{path}", "custom_url")

    @staticmethod
    def _host_label(url: str) -> str:
        host = urlsplit(url).netloc.casefold()
        labels = [label for label in host.split(".") if label not in {"www", "jobs", "boards", "careers"}]
        if not labels:
            return host
        return labels[0].replace("-", " ").title()

    @staticmethod
    def _host_is_excluded(url: str) -> bool:
        host = urlsplit(url).netloc.casefold()
        return any(host.endswith(excluded) for excluded in EXCLUDED_HOSTS)

    @staticmethod
    def _looks_like_careers_page(url: str) -> bool:
        path = urlsplit(url).path.casefold()
        return any(token in path for token in ("/careers", "/career", "/jobs", "/join-us", "/joinus"))

    @staticmethod
    def _should_default_playwright(url: str, platform: str) -> bool:
        host = urlsplit(url).netloc.casefold()
        return platform == "workday" or "workdayjobs.com" in host or "myworkdayjobs.com" in host

    @staticmethod
    def _career_links_from_page(soup: BeautifulSoup, base_url: str) -> list[str]:
        links: list[str] = []
        for anchor in soup.select("a[href]"):
            href = absolute_url(base_url, anchor.get("href"))
            text = normalize_whitespace(anchor.get_text(" ")).casefold()
            if not href:
                continue
            if any(token in text for token in ("careers", "jobs", "join us", "join-us", "open roles", "open positions")):
                links.append(href)
                continue
            if SourceDiscovery._looks_like_careers_page(href):
                links.append(href)
        deduped = OrderedDict((sanitize_source_url(link, "custom_url"), None) for link in links)
        return list(deduped.keys())

    @staticmethod
    def _dedupe_candidates(candidates: list[DiscoveredSourceCandidate]) -> list[DiscoveredSourceCandidate]:
        deduped: OrderedDict[str, DiscoveredSourceCandidate] = OrderedDict()
        for candidate in candidates:
            key = f"{candidate.source_type}|{candidate.url}"
            existing = deduped.get(key)
            if existing is None:
                deduped[key] = candidate
                continue
            preferred = existing
            if candidate.platform in {"greenhouse", "lever"} and existing.platform not in {"greenhouse", "lever"}:
                preferred = candidate
            elif len(candidate.reason) < len(existing.reason):
                preferred = candidate
            deduped[key] = preferred
        return sorted(
            deduped.values(),
            key=lambda candidate: (
                SourceDiscovery._candidate_priority(candidate),
                candidate.name.casefold(),
                candidate.url,
            ),
        )

    @staticmethod
    def _candidate_priority(candidate: DiscoveredSourceCandidate) -> int:
        if candidate.platform in {"greenhouse", "lever"}:
            return 0
        if candidate.platform in {"ashby", "smartrecruiters", "jobvite", "bamboohr", "workday"}:
            return 1
        if candidate.platform == "careers page":
            return 3
        return 2
