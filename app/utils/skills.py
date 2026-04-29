from __future__ import annotations

import re
from functools import lru_cache

from .text import normalize_whitespace, unique_sorted

SKILL_CATALOG: dict[str, dict[str, object]] = {
    "AWS": {"aliases": ["aws", "amazon web services"], "category": "cloud"},
    "Azure": {"aliases": ["azure", "microsoft azure"], "category": "cloud"},
    "GCP": {"aliases": ["gcp", "google cloud", "google cloud platform"], "category": "cloud"},
    "Python": {"aliases": ["python"], "category": "language"},
    "Java": {"aliases": ["java"], "category": "language"},
    "JavaScript": {"aliases": ["javascript", "js"], "category": "language"},
    "TypeScript": {"aliases": ["typescript", "ts"], "category": "language"},
    "C++": {"aliases": ["c++", "cpp"], "category": "language"},
    "C#": {"aliases": ["c#", ".net", "dotnet"], "category": "language"},
    "Go": {"aliases": ["golang", "go"], "category": "language"},
    "Rust": {"aliases": ["rust"], "category": "language"},
    "SQL": {"aliases": ["sql", "postgresql", "mysql", "sqlite"], "category": "data"},
    "PostgreSQL": {"aliases": ["postgresql", "postgres"], "category": "data"},
    "MongoDB": {"aliases": ["mongodb", "mongo db", "mongo"], "category": "data"},
    "Redis": {"aliases": ["redis"], "category": "data"},
    "Spark": {"aliases": ["spark", "apache spark", "pyspark"], "category": "data"},
    "Airflow": {"aliases": ["airflow", "apache airflow"], "category": "tool"},
    "Docker": {"aliases": ["docker"], "category": "tool"},
    "Kubernetes": {"aliases": ["kubernetes", "k8s"], "category": "tool"},
    "Terraform": {"aliases": ["terraform"], "category": "tool"},
    "Ansible": {"aliases": ["ansible"], "category": "tool"},
    "Linux": {"aliases": ["linux", "unix"], "category": "platform"},
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
    "Networking": {"aliases": ["networking", "tcp/ip", "dns"], "category": "platform"},
    "Agile": {"aliases": ["agile", "scrum", "kanban"], "category": "process"},
    "Product Management": {"aliases": ["product management"], "category": "process"},
    "Technical Writing": {"aliases": ["technical writing"], "category": "process"},
}

TOOL_CATEGORIES = {"tool", "platform", "cloud", "data", "analytics", "runtime"}
CLEARANCE_PATTERNS = {
    "Public Trust": [r"\bpublic trust\b"],
    "Secret": [r"\bsecret clearance\b", r"\bsecret\b"],
    "Top Secret": [r"\btop secret\b", r"\bts\b"],
    "TS/SCI": [r"\bts\/sci\b", r"\btop secret\/sci\b"],
    "Polygraph": [r"\bpoly(graph|graphed)\b"],
}
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


@lru_cache(maxsize=1)
def _compiled_skill_patterns() -> dict[str, list[re.Pattern[str]]]:
    compiled: dict[str, list[re.Pattern[str]]] = {}
    for canonical, meta in SKILL_CATALOG.items():
        aliases = meta["aliases"]
        compiled[canonical] = [re.compile(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", re.IGNORECASE) for alias in aliases]
    return compiled


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


def extract_clearance_terms(text: str | None) -> list[str]:
    haystack = normalize_whitespace(text).casefold()
    if not haystack:
        return []
    matches: list[str] = []
    for canonical, patterns in CLEARANCE_PATTERNS.items():
        if any(re.search(pattern, haystack, flags=re.IGNORECASE) for pattern in patterns):
            matches.append(canonical)
    return unique_sorted(matches)


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
