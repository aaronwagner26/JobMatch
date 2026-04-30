from __future__ import annotations

import re
from functools import lru_cache

from .text import normalize_whitespace, unique_sorted

SKILL_CATALOG: dict[str, dict[str, object]] = {
    "AWS": {"aliases": ["aws", "amazon web services"], "category": "cloud"},
    "Azure": {"aliases": ["azure", "microsoft azure"], "category": "cloud"},
    "GCP": {"aliases": ["gcp", "google cloud", "google cloud platform"], "category": "cloud"},
    "Python": {"aliases": ["python"], "category": "language"},
    "PowerShell": {"aliases": ["powershell", "pwsh"], "category": "language"},
    "Bash": {"aliases": ["bash", "shell scripting"], "category": "language"},
    "Java": {"aliases": ["java"], "category": "language"},
    "JavaScript": {"aliases": ["javascript", "js"], "category": "language"},
    "TypeScript": {"aliases": ["typescript", "ts"], "category": "language"},
    "C++": {"aliases": ["c++", "cpp"], "category": "language"},
    "C#": {"aliases": ["c#", ".net", "dotnet"], "category": "language"},
    "Go": {"aliases": ["golang", "go"], "category": "language"},
    "Rust": {"aliases": ["rust"], "category": "language"},
    "SQL": {"aliases": ["sql", "transact-sql", "t-sql"], "category": "data"},
    "PostgreSQL": {"aliases": ["postgresql", "postgres"], "category": "data"},
    "MySQL": {"aliases": ["mysql"], "category": "data"},
    "MongoDB": {"aliases": ["mongodb", "mongo db", "mongo"], "category": "data"},
    "Redis": {"aliases": ["redis"], "category": "data"},
    "Spark": {"aliases": ["spark", "apache spark", "pyspark"], "category": "data"},
    "Airflow": {"aliases": ["airflow", "apache airflow"], "category": "tool"},
    "Docker": {"aliases": ["docker"], "category": "tool"},
    "Kubernetes": {"aliases": ["kubernetes", "k8s"], "category": "tool"},
    "Terraform": {"aliases": ["terraform"], "category": "tool"},
    "Ansible": {"aliases": ["ansible"], "category": "tool"},
    "Linux": {"aliases": ["linux", "unix"], "category": "platform"},
    "Windows Server": {
        "aliases": ["windows server", "windows administration", "windows infrastructure"],
        "category": "platform",
    },
    "Active Directory": {"aliases": ["active directory", "ad ds"], "category": "platform"},
    "Entra ID": {"aliases": ["entra id", "azure ad", "azure active directory"], "category": "platform"},
    "Office 365": {"aliases": ["office 365", "microsoft 365", "o365", "m365"], "category": "platform"},
    "Exchange": {"aliases": ["exchange", "exchange online"], "category": "platform"},
    "VMware": {"aliases": ["vmware", "vsphere", "esxi", "vcenter"], "category": "platform"},
    "Hyper-V": {"aliases": ["hyper-v"], "category": "platform"},
    "SCCM": {"aliases": ["sccm", "configmgr", "configuration manager"], "category": "tool"},
    "Intune": {"aliases": ["intune", "microsoft intune", "endpoint manager"], "category": "tool"},
    "Jamf": {"aliases": ["jamf"], "category": "tool"},
    "Okta": {"aliases": ["okta"], "category": "security"},
    "Git": {"aliases": ["git", "github", "gitlab", "bitbucket"], "category": "tool"},
    "CI/CD": {"aliases": ["ci/cd", "continuous integration", "continuous delivery"], "category": "tool"},
    "Jenkins": {"aliases": ["jenkins"], "category": "tool"},
    "GitHub Actions": {"aliases": ["github actions"], "category": "tool"},
    "React": {"aliases": ["react", "reactjs", "react.js"], "category": "framework"},
    "Next.js": {"aliases": ["next.js", "nextjs"], "category": "framework"},
    "Vue": {"aliases": ["vue", "vue.js", "vuejs"], "category": "framework"},
    "Angular": {"aliases": ["angular"], "category": "framework"},
    "Node.js": {"aliases": ["node.js", "nodejs"], "category": "runtime"},
    "FastAPI": {"aliases": ["fastapi"], "category": "framework"},
    "Django": {"aliases": ["django"], "category": "framework"},
    "Flask": {"aliases": ["flask"], "category": "framework"},
    "NiceGUI": {"aliases": ["nicegui"], "category": "framework"},
    "GraphQL": {"aliases": ["graphql"], "category": "api"},
    "REST APIs": {"aliases": ["rest api", "restful api", "restful services"], "category": "api"},
    "Microservices": {"aliases": ["microservices", "distributed systems"], "category": "architecture"},
    "Data Engineering": {"aliases": ["data engineering", "etl", "elt"], "category": "domain"},
    "Machine Learning": {"aliases": ["machine learning", "ml"], "category": "domain"},
    "NLP": {"aliases": ["nlp", "natural language processing"], "category": "domain"},
    "LLMs": {"aliases": ["llm", "llms", "large language models"], "category": "domain"},
    "PyTorch": {"aliases": ["pytorch"], "category": "ml"},
    "TensorFlow": {"aliases": ["tensorflow"], "category": "ml"},
    "Pandas": {"aliases": ["pandas"], "category": "data"},
    "NumPy": {"aliases": ["numpy"], "category": "data"},
    "Tableau": {"aliases": ["tableau"], "category": "analytics"},
    "Power BI": {"aliases": ["power bi", "powerbi"], "category": "analytics"},
    "Excel": {"aliases": ["excel", "microsoft excel"], "category": "tool"},
    "Figma": {"aliases": ["figma"], "category": "tool"},
    "Playwright": {"aliases": ["playwright"], "category": "tool"},
    "Selenium": {"aliases": ["selenium"], "category": "tool"},
    "Kafka": {"aliases": ["kafka", "apache kafka"], "category": "tool"},
    "RabbitMQ": {"aliases": ["rabbitmq"], "category": "tool"},
    "Snowflake": {"aliases": ["snowflake"], "category": "data"},
    "Databricks": {"aliases": ["databricks"], "category": "data"},
    "Splunk": {"aliases": ["splunk"], "category": "security"},
    "SIEM": {"aliases": ["siem"], "category": "security"},
    "Zero Trust": {"aliases": ["zero trust"], "category": "security"},
    "IAM": {"aliases": ["iam", "identity and access management"], "category": "security"},
    "SOC 2": {"aliases": ["soc 2", "soc2"], "category": "security"},
    "FedRAMP": {"aliases": ["fedramp"], "category": "security"},
    "NIST": {"aliases": ["nist"], "category": "security"},
    "DevSecOps": {"aliases": ["devsecops"], "category": "security"},
    "Networking": {"aliases": ["networking", "tcp/ip", "dns", "routing", "switching"], "category": "platform"},
    "Cisco": {"aliases": ["cisco", "ios xe", "nx-os"], "category": "platform"},
    "Palo Alto": {"aliases": ["palo alto", "pan-os", "panorama"], "category": "security"},
    "Fortinet": {"aliases": ["fortinet", "fortigate"], "category": "security"},
    "Agile": {"aliases": ["agile", "scrum", "kanban"], "category": "process"},
    "Product Management": {"aliases": ["product management"], "category": "process"},
    "Technical Writing": {"aliases": ["technical writing"], "category": "process"},
}

CERTIFICATION_CATALOG: dict[str, list[str]] = {
    "Security+": ["security+", "comptia security plus", "comptia security+"],
    "Network+": ["network+", "comptia network+"],
    "A+": ["comptia a+"],
    "CISSP": ["cissp"],
    "CASP+": ["casp+", "comptia casp+"],
    "CySA+": ["cysa+", "comptia cysa+"],
    "AWS Solutions Architect": ["aws certified solutions architect", "aws solutions architect"],
    "AWS SysOps Administrator": ["aws certified sysops administrator", "aws sysops administrator"],
    "Azure Administrator": ["azure administrator", "az-104"],
    "Azure Security Engineer": ["azure security engineer", "az-500"],
    "CCNA": ["ccna"],
    "CCNP": ["ccnp"],
    "ITIL": ["itil"],
    "PMP": ["pmp", "project management professional"],
    "RHCSA": ["rhcsa"],
    "RHCE": ["rhce"],
}

TOOL_CATEGORIES = {"tool", "platform", "cloud", "data", "analytics", "runtime"}

CLEARANCE_ORDER = [
    "TS/SCI",
    "Top Secret",
    "Secret",
    "Confidential",
    "Public Trust",
    "Full Scope Polygraph",
    "CI Polygraph",
    "Polygraph",
]
CLEARANCE_LEVEL_PATTERNS = {
    "TS/SCI": [
        r"\bts\s*\/\s*sci\b",
        r"\btop secret\s*\/\s*sci\b",
        r"\btop secret sensitive compartmented information\b",
    ],
    "Top Secret": [
        r"\btop secret\b",
        r"\bts clearance\b",
        r"\bactive ts\b",
        r"\bcurrent ts\b",
    ],
    "Secret": [
        r"\bsecret clearance\b",
        r"\bactive secret\b",
        r"\bcurrent secret\b",
        r"\bsecret level\b",
        r"\bsecret eligible\b",
        r"\beligible for secret\b",
        r"\bability to obtain (?:an? )?secret\b",
    ],
    "Confidential": [
        r"\bconfidential clearance\b",
        r"\bconfidential level\b",
    ],
    "Public Trust": [
        r"\bpublic trust\b",
        r"\bhigh public trust\b",
        r"\bmoderate public trust\b",
    ],
}
CLEARANCE_CONTEXT_RE = re.compile(
    r"\b(clearance|clearances|cleared|polygraph|poly|adjudicat|investigation|"
    r"eligible|eligibility|obtain|maintain|maintained|access|sci|sap|dod|federal|public trust)\b",
    re.IGNORECASE,
)
CLEARANCE_ACTIVE_RE = re.compile(
    r"\b(active|current|existing|adjudicated|held|maintain(?:ed)?|possess(?:es|ed)?)\b",
    re.IGNORECASE,
)
CLEARANCE_OBTAIN_RE = re.compile(
    r"\b(eligible|eligibility|ability)\b.*\b(obtain|maintain)\b|\bmust be able to obtain\b",
    re.IGNORECASE,
)
FULL_SCOPE_POLY_RE = re.compile(r"\b(full[- ]scope|fs)\s+poly(graph)?\b", re.IGNORECASE)
CI_POLY_RE = re.compile(r"\b(ci|counterintelligence)\s+poly(graph)?\b", re.IGNORECASE)
POLY_RE = re.compile(r"\bpoly(graph|graphed)?\b", re.IGNORECASE)
CLEARANCE_PATTERNS = {
    **CLEARANCE_LEVEL_PATTERNS,
    "Full Scope Polygraph": [FULL_SCOPE_POLY_RE.pattern],
    "CI Polygraph": [CI_POLY_RE.pattern],
    "Polygraph": [POLY_RE.pattern],
}
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")

REMOTE_PATTERNS = {
    "remote": [r"\bremote\b", r"\bwork from home\b", r"\bdistributed\b"],
    "hybrid": [r"\bhybrid\b", r"\bremote\/onsite\b"],
    "on-site": [r"\bon[- ]site\b", r"\bin office\b", r"\bon site\b"],
}
JOB_TYPE_PATTERNS = {
    "full-time": [r"\bfull[- ]time\b"],
    "part-time": [r"\bpart[- ]time\b"],
    "contract": [r"\bcontract\b", r"\bcontractor\b", r"\b1099\b"],
    "temporary": [r"\btemporary\b", r"\btemp\b"],
    "internship": [r"\bintern(ship)?\b"],
    "apprenticeship": [r"\bapprenticeship\b"],
}

SALARY_RANGE_PATTERNS = [
    re.compile(
        r"(?P<prefix>\bbetween\b|\bfrom\b)?\s*(?P<currency1>\$|usd)?\s*"
        r"(?P<min>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)\s*"
        r"(?:-|to|and|through|up to|–|—)\s*"
        r"(?P<currency2>\$|usd)?\s*(?P<max>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)"
        r"(?P<suffix>[^.;,\n]{0,48})",
        re.IGNORECASE,
    ),
]
SALARY_SINGLE_PATTERNS = [
    re.compile(
        r"\b(?:salary|compensation|pay range|pay|hourly rate)\b[^$0-9]{0,20}"
        r"(?P<currency>\$|usd)?\s*(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)"
        r"(?P<suffix>[^.;,\n]{0,40})",
        re.IGNORECASE,
    ),
]
SALARY_RANGE_PATTERNS.extend(
    [
        re.compile(
            r"(?P<prefix>\bbetween\b|\bfrom\b)?\s*(?P<currency1>\$|usd)?\s*"
            r"(?P<min>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)\s*"
            r"(?:–|—)\s*"
            r"(?P<currency2>\$|usd)?\s*(?P<max>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)"
            r"(?P<suffix>[^.;,\n]{0,48})",
            re.IGNORECASE,
        ),
    ]
)
SALARY_SINGLE_PATTERNS.extend(
    [
        re.compile(
            r"\b(?:starting at|starts at|from|up to|minimum of|maximum of)\b[^$0-9]{0,12}"
            r"(?P<currency>\$|usd)?\s*(?P<amount>\d{1,3}(?:,\d{3})*(?:\.\d+)?(?:\s*[km])?)"
            r"(?P<suffix>[^.;,\n]{0,40})",
            re.IGNORECASE,
        ),
    ]
)
SALARY_INTERVAL_PATTERNS = {
    "hour": [r"\bper hour\b", r"\ban hour\b", r"\bhourly\b", r"\/hr\b", r"\bhr\b"],
    "year": [r"\bper year\b", r"\byearly\b", r"\bannual(?:ly)?\b", r"\/yr\b", r"\ba year\b"],
    "month": [r"\bper month\b", r"\bmonthly\b", r"\/mo\b"],
    "week": [r"\bper week\b", r"\bweekly\b", r"\/wk\b"],
    "day": [r"\bper day\b", r"\bdaily\b", r"\/day\b"],
}


@lru_cache(maxsize=1)
def _compiled_skill_patterns() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for canonical, meta in SKILL_CATALOG.items():
        aliases = meta["aliases"]
        compiled[canonical] = [re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.IGNORECASE) for alias in aliases]
    return compiled


@lru_cache(maxsize=1)
def _compiled_cert_patterns() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for canonical, aliases in CERTIFICATION_CATALOG.items():
        compiled[canonical] = [re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.IGNORECASE) for alias in aliases]
    return compiled


@lru_cache(maxsize=1)
def _skill_alias_lookup() -> dict[str, str]:
    lookup: dict[str, str] = {}
    for canonical, meta in SKILL_CATALOG.items():
        for phrase in [canonical, *meta["aliases"]]:
            normalized = _normalize_skill_phrase(str(phrase))
            if normalized:
                lookup[normalized] = canonical
    return lookup


def extract_skills(text: str | None) -> list[str]:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return []
    matches: list[str] = []
    for canonical, patterns in _compiled_skill_patterns().items():
        if any(pattern.search(haystack) for pattern in patterns):
            matches.append(canonical)
    return unique_sorted(matches)


def extract_tools(text: str | None) -> list[str]:
    skills = extract_skills(text)
    tools = [skill for skill in skills if SKILL_CATALOG[skill]["category"] in TOOL_CATEGORIES]
    return unique_sorted(tools)


def extract_certifications(text: str | None) -> list[str]:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return []
    matches: list[str] = []
    for canonical, patterns in _compiled_cert_patterns().items():
        if any(pattern.search(haystack) for pattern in patterns):
            matches.append(canonical)
    return unique_sorted(matches)


def extract_clearance_terms(text: str | None) -> list[str]:
    return extract_clearance_info(text)["terms"]


def extract_clearance_info(text: str | None) -> dict[str, object]:
    normalized = normalize_whitespace(text)
    if not normalized:
        return {"terms": [], "summary": "", "active": False, "obtainable": False}

    terms: list[str] = []
    active = False
    obtainable = False
    for sentence in _clearance_sentences(normalized):
        lowered = sentence.casefold()
        if not _looks_like_clearance_sentence(lowered):
            continue

        sentence_terms = _clearance_terms_from_sentence(sentence)
        if not sentence_terms:
            continue
        terms.extend(sentence_terms)
        if CLEARANCE_ACTIVE_RE.search(sentence):
            active = True
        if CLEARANCE_OBTAIN_RE.search(sentence):
            obtainable = True

    ordered_terms = _ordered_unique(terms, CLEARANCE_ORDER)
    summary_parts = list(ordered_terms)
    qualifiers: list[str] = []
    if active:
        qualifiers.append("active/current")
    if obtainable:
        qualifiers.append("eligible to obtain")
    if qualifiers:
        summary_parts.append(f"({', '.join(qualifiers)})")

    return {
        "terms": ordered_terms,
        "summary": " ".join(summary_parts).strip(),
        "active": active,
        "obtainable": obtainable,
    }


def detect_remote_mode(text: str | None) -> str:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return "unknown"
    for mode, patterns in REMOTE_PATTERNS.items():
        if any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in patterns):
            return mode
    return "unknown"


def detect_job_type(text: str | None) -> str | None:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return None
    for label, patterns in JOB_TYPE_PATTERNS.items():
        if any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in patterns):
            return label
    return None


def extract_salary_info(text: str | None) -> dict[str, object]:
    normalized = normalize_whitespace(text)
    if not normalized:
        return {"minimum": None, "maximum": None, "currency": None, "interval": None, "display": None}

    for pattern in SALARY_RANGE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        window = normalized[max(0, match.start() - 24) : min(len(normalized), match.end() + 24)]
        has_currency = bool(match.group("currency1") or match.group("currency2") or "$" in window or "usd" in window.casefold())
        has_compensation_context = bool(
            re.search(r"\b(salary|compensation|pay|rate|hourly|annual|base)\b", window, flags=re.IGNORECASE)
        )
        if not has_currency and not has_compensation_context:
            continue
        if re.search(r"\byears?(?:\s+of)?\s+experience\b", window, flags=re.IGNORECASE):
            continue
        minimum = _parse_salary_amount(match.group("min"))
        maximum = _parse_salary_amount(match.group("max"))
        if minimum is None or maximum is None or maximum < minimum:
            continue
        interval = _detect_salary_interval(match.group("suffix") or normalized)
        currency = "USD" if (match.group("currency1") or match.group("currency2") or "$") else "USD"
        return {
            "minimum": minimum,
            "maximum": maximum,
            "currency": currency,
            "interval": interval,
            "display": format_salary_display(minimum, maximum, currency=currency, interval=interval),
        }

    for pattern in SALARY_SINGLE_PATTERNS:
        match = pattern.search(normalized)
        if not match:
            continue
        window = normalized[max(0, match.start() - 24) : min(len(normalized), match.end() + 24)]
        has_currency = bool(match.groupdict().get("currency") or "$" in window or "usd" in window.casefold())
        has_compensation_context = bool(
            re.search(r"\b(salary|compensation|pay|rate|hourly|annual|base|starting at|starts at|minimum of|maximum of|up to)\b", window, flags=re.IGNORECASE)
        )
        if not has_currency and not has_compensation_context:
            continue
        if re.search(r"\byears?(?:\s+of)?\s+experience\b", window, flags=re.IGNORECASE):
            continue
        amount = _parse_salary_amount(match.group("amount"))
        if amount is None:
            continue
        interval = _detect_salary_interval(match.group("suffix") or normalized)
        currency = "USD" if (match.group("currency") or "$") else "USD"
        return {
            "minimum": amount,
            "maximum": amount,
            "currency": currency,
            "interval": interval,
            "display": format_salary_display(amount, amount, currency=currency, interval=interval),
        }

    return {"minimum": None, "maximum": None, "currency": None, "interval": None, "display": None}


def format_salary_display(
    minimum: float | None,
    maximum: float | None,
    *,
    currency: str | None = "USD",
    interval: str | None = None,
) -> str | None:
    if minimum is None and maximum is None:
        return None
    prefix = "$" if (currency or "USD").upper() == "USD" else f"{currency or ''} "
    minimum_text = _format_salary_amount(minimum)
    maximum_text = _format_salary_amount(maximum)
    if minimum is not None and maximum is not None and round(minimum, 2) != round(maximum, 2):
        display = f"{prefix}{minimum_text} - {prefix}{maximum_text}"
    else:
        display = f"{prefix}{minimum_text or maximum_text}"
    if interval:
        display += {
            "hour": "/hr",
            "day": "/day",
            "week": "/wk",
            "month": "/mo",
            "year": "/yr",
        }.get(interval, "")
    return display


def canonicalize_skill_name(value: str | None) -> str:
    normalized = _normalize_skill_phrase(value)
    if not normalized:
        return ""
    return _skill_alias_lookup().get(normalized, normalize_whitespace(value))


def skills_equivalent(left: str | None, right: str | None) -> bool:
    left_canonical = canonicalize_skill_name(left)
    right_canonical = canonicalize_skill_name(right)
    if left_canonical and right_canonical and left_canonical.casefold() == right_canonical.casefold():
        return True

    left_aliases = _skill_equivalent_phrases(left)
    right_aliases = _skill_equivalent_phrases(right)
    if left_aliases & right_aliases:
        return True

    left_tokens = _meaningful_skill_tokens(left_aliases)
    right_tokens = _meaningful_skill_tokens(right_aliases)
    if not left_tokens or not right_tokens:
        return False
    if left_tokens <= right_tokens or right_tokens <= left_tokens:
        return True
    overlap = left_tokens & right_tokens
    return len(overlap) >= 2 and len(overlap) >= min(len(left_tokens), len(right_tokens))


def match_skills(resume_skills: list[str], job_skills: list[str]) -> tuple[list[str], list[str]]:
    matched: list[str] = []
    missing: list[str] = []
    for job_skill in unique_sorted(job_skills):
        if any(skills_equivalent(resume_skill, job_skill) for resume_skill in resume_skills):
            matched.append(job_skill)
        else:
            missing.append(job_skill)
    return matched, missing


def _clearance_sentences(text: str) -> list[str]:
    sentences = [normalize_whitespace(part) for part in SENTENCE_SPLIT_RE.split(text) if normalize_whitespace(part)]
    return sentences or [normalize_whitespace(text)]


def _looks_like_clearance_sentence(sentence: str) -> bool:
    if not sentence:
        return False
    if "top secret" in sentence or "ts/sci" in sentence or "public trust" in sentence:
        return True
    return bool(CLEARANCE_CONTEXT_RE.search(sentence))


def _clearance_terms_from_sentence(sentence: str) -> list[str]:
    matches: list[str] = []
    for canonical, patterns in CLEARANCE_LEVEL_PATTERNS.items():
        if any(re.search(pattern, sentence, flags=re.IGNORECASE) for pattern in patterns):
            if canonical == "Top Secret" and re.search(r"\bts\s*\/\s*sci\b", sentence, flags=re.IGNORECASE):
                continue
            if canonical == "Secret" and re.search(r"\btop secret\b", sentence, flags=re.IGNORECASE):
                continue
            matches.append(canonical)
    if FULL_SCOPE_POLY_RE.search(sentence):
        matches.append("Full Scope Polygraph")
    elif CI_POLY_RE.search(sentence):
        matches.append("CI Polygraph")
    elif POLY_RE.search(sentence):
        matches.append("Polygraph")
    return _ordered_unique(matches, CLEARANCE_ORDER)


def _ordered_unique(values: list[str], order: list[str]) -> list[str]:
    priority = {value: index for index, value in enumerate(order)}
    deduped: dict[str, str] = {}
    for value in values:
        normalized = normalize_whitespace(value)
        if normalized:
            deduped.setdefault(normalized.casefold(), normalized)
    return sorted(deduped.values(), key=lambda item: (priority.get(item, len(order)), item.casefold()))


def _parse_salary_amount(value: str | None) -> float | None:
    normalized = normalize_whitespace(value)
    if not normalized:
        return None
    multiplier = 1.0
    if normalized[-1:].casefold() == "k":
        multiplier = 1000.0
        normalized = normalized[:-1].strip()
    elif normalized[-1:].casefold() == "m":
        multiplier = 1_000_000.0
        normalized = normalized[:-1].strip()
    numeric = normalized.replace(",", "")
    try:
        amount = float(numeric) * multiplier
    except ValueError:
        return None
    return round(amount, 2)


def _format_salary_amount(value: float | None) -> str:
    if value is None:
        return ""
    if value >= 1000:
        return f"{int(round(value)):,.0f}"
    if value.is_integer():
        return f"{int(value)}"
    return f"{value:.2f}"


def _detect_salary_interval(text: str | None) -> str | None:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return None
    for interval, patterns in SALARY_INTERVAL_PATTERNS.items():
        if any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in patterns):
            return interval
    return "year"


def _normalize_skill_phrase(value: str | None) -> str:
    text = normalize_whitespace(value).casefold()
    if not text:
        return ""
    text = text.replace("c plus plus", "c++").replace("c sharp", "c#")
    text = re.sub(r"[^a-z0-9+#]+", " ", text)
    return normalize_whitespace(text)


def _skill_equivalent_phrases(value: str | None) -> set[str]:
    canonical = canonicalize_skill_name(value)
    phrases: set[str] = set()
    if canonical and canonical in SKILL_CATALOG:
        for phrase in [canonical, *SKILL_CATALOG[canonical]["aliases"]]:
            normalized = _normalize_skill_phrase(str(phrase))
            if normalized:
                phrases.add(normalized)
    normalized_value = _normalize_skill_phrase(value)
    if normalized_value:
        phrases.add(normalized_value)
    return phrases


def _meaningful_skill_tokens(phrases: set[str]) -> set[str]:
    tokens: set[str] = set()
    for phrase in phrases:
        for token in phrase.split():
            if len(token) >= 3 or token in {"c#", "c++", "ci", "ai", "ml", "ui", "ux"}:
                tokens.add(token)
    return tokens
