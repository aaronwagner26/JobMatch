from __future__ import annotations

from app.core.source_discovery import SourceDiscovery


def test_discover_from_company_homepage_finds_greenhouse_board() -> None:
    discovery = SourceDiscovery()

    def fake_fetch_html(url: str) -> str | None:
        if url == "https://example.com/":
            return """
            <html>
              <body>
                <a href="https://boards.greenhouse.io/exampleco/jobs/12345">Senior Platform Engineer</a>
              </body>
            </html>
            """
        return None

    discovery._fetch_html = fake_fetch_html  # type: ignore[method-assign]

    candidates = discovery.discover("https://example.com")

    assert len(candidates) == 1
    assert candidates[0].platform == "greenhouse"
    assert candidates[0].source_type == "greenhouse"
    assert candidates[0].url == "https://boards.greenhouse.io/exampleco"
    assert candidates[0].identifier == "exampleco"


def test_discover_from_company_name_uses_search_results() -> None:
    discovery = SourceDiscovery()

    def fake_search_results(query: str) -> list[tuple[str, str]]:
        assert query == "OpenAI"
        return [
            ("Careers | OpenAI", "https://openai.com/careers/"),
            ("OpenAI Jobs", "https://jobs.ashbyhq.com/openai"),
        ]

    def fake_fetch_html(url: str) -> str | None:
        if url == "https://openai.com/careers":
            return """
            <html>
              <body>
                <a href="https://jobs.ashbyhq.com/openai?gh_src=test">Open roles</a>
              </body>
            </html>
            """
        return None

    discovery._search_results = fake_search_results  # type: ignore[method-assign]
    discovery._fetch_html = fake_fetch_html  # type: ignore[method-assign]

    candidates = discovery.discover("OpenAI")

    assert candidates
    assert candidates[0].platform == "ashby"
    assert candidates[0].url == "https://jobs.ashbyhq.com/openai"
    assert candidates[0].source_type == "custom_url"


def test_discovery_ignores_linkedin_company_jobs_pages() -> None:
    discovery = SourceDiscovery()

    def fake_search_results(query: str) -> list[tuple[str, str]]:
        assert query == "Netflix"
        return [
            ("Netflix Jobs | LinkedIn", "https://www.linkedin.com/company/netflix/jobs"),
            ("Netflix Careers", "https://explore.jobs.netflix.net/careers"),
        ]

    def fake_fetch_html(url: str) -> str | None:
        if url == "https://explore.jobs.netflix.net/careers":
            return """
            <html>
              <body>
                <a href="https://explore.jobs.netflix.net/careers/job/123">Senior Software Engineer</a>
              </body>
            </html>
            """
        return None

    discovery._search_results = fake_search_results  # type: ignore[method-assign]
    discovery._fetch_html = fake_fetch_html  # type: ignore[method-assign]

    candidates = discovery.discover("Netflix")

    assert candidates
    assert all("linkedin.com" not in candidate.url for candidate in candidates)
    assert candidates[0].url == "https://explore.jobs.netflix.net/careers"
