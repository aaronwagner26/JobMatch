from app.core.job_fetcher import JobFetcher
from app.core.normalizer import JobNormalizer
from app.core.types import JobSourceConfig
from app.utils.skills import extract_salary_info


def test_extract_salary_info_prefers_real_hourly_range_over_experience_range() -> None:
    info = extract_salary_info("$50 | Hourly pay | 1-5 years | Pay: $50.00 - $100.00 per hour")

    assert info["minimum"] == 50.0
    assert info["maximum"] == 100.0
    assert info["interval"] == "hour"
    assert info["display"] == "$50 - $100/hr"


def test_extract_salary_info_does_not_invent_yearly_interval_for_bare_amount() -> None:
    info = extract_salary_info("$50")

    assert info["minimum"] is None
    assert info["maximum"] is None
    assert info["interval"] is None
    assert info["display"] is None


def test_parse_job_detail_payload_reads_indeed_embedded_salary_container() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=1,
        name="Indeed Search",
        source_type="indeed",
        url="https://www.indeed.com/jobs?q=data+engineer&l=Remote",
    )
    html = """
    <html>
      <body>
        <h1>Senior Data Engineer (AI/ML and AWS Cloud)</h1>
        <div data-testid="inlineHeader-companyName">Pantheon Data</div>
        <div data-testid="inlineHeader-companyLocation">Charlotte, NC • Remote</div>
        <div id="salaryInfoAndJobType">
          <span>$140,000 - $175,000 a year</span>
          <span> - Full-time</span>
        </div>
        <div id="jobDescriptionText">
          <p>The salary range for this position is $140,000 - $175,000.</p>
        </div>
      </body>
    </html>
    """

    payload = fetcher._parse_job_detail_payload(html, "https://www.indeed.com/viewjob?jk=abc123", source)

    assert payload is not None
    assert payload["salary_text"].startswith("$140,000 - $175,000 a year")


def test_parse_job_detail_payload_reads_hourly_pay_from_indeed_job_details_section() -> None:
    fetcher = JobFetcher(JobNormalizer())
    source = JobSourceConfig(
        id=1,
        name="Indeed Search",
        source_type="indeed",
        url="https://www.indeed.com/jobs?q=cybersecurity&l=Remote",
    )
    html = """
    <html>
      <body>
        <h1>OT Cybersecurity specialist - Remote</h1>
        <div data-testid="inlineHeader-companyName">Calance US</div>
        <div data-testid="inlineHeader-companyLocation">Houston, TX 77010 • Remote</div>
        <div id="salaryInfoAndJobType">
          <span>$70 - $85 an hour</span>
          <span> - Contract</span>
        </div>
        <div id="jobDetailsSection">
          <div aria-label="Pay">
            <ul>
              <li>$70 - $85 an hour</li>
            </ul>
          </div>
          <div aria-label="Job type">
            <ul>
              <li>Contract</li>
            </ul>
          </div>
          <div aria-label="Work setting">
            <ul>
              <li>Remote</li>
            </ul>
          </div>
        </div>
        <div id="jobDescriptionText">
          <p>OT security generalist role with Windows and Splunk experience.</p>
        </div>
      </body>
    </html>
    """

    payload = fetcher._parse_job_detail_payload(html, "https://www.indeed.com/viewjob?jk=3f4fd920a4f8a8b4", source)

    assert payload is not None
    assert payload["salary_text"] == "$70 - $85 an hour"
    assert "Contract" in payload["employment_text"]
