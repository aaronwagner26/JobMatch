from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"
BROWSER_PROFILES_DIR = DATA_DIR / "browser_profiles"
DB_PATH = DATA_DIR / "jobmatch.sqlite3"

APP_NAME = "JobMatch"
DEFAULT_MODEL_NAME = os.getenv("JOBMATCH_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_SCAN_CONCURRENCY = 4
DEFAULT_DETAIL_FETCH_LIMIT = 10
DEFAULT_DETAIL_FETCH_CONCURRENCY = 2
DEFAULT_HTTP_TIMEOUT = 25.0
DEFAULT_SOURCE_MAX_PAGES = 3
DEFAULT_SOURCE_REQUEST_DELAY_MS = 750
DEFAULT_REQUEST_MAX_RETRIES = 2
DEFAULT_REQUEST_BACKOFF_MULTIPLIER = 2.0
DEFAULT_EARLY_STOP_MIN_PAGES = 3
DEFAULT_EARLY_STOP_CONSECUTIVE_PAGES = 2
DEFAULT_EARLY_STOP_KNOWN_RATIO = 0.85
DEFAULT_BROWSER_CHALLENGE_WAIT_SECONDS = 75

SOURCE_TYPES = [
    "auto",
    "browser_capture",
    "greenhouse",
    "lever",
    "indeed",
    "clearance",
    "custom_url",
]

REMOTE_MODES = ["any", "remote", "hybrid", "on-site", "unknown"]
JOB_TYPES = [
    "any",
    "full-time",
    "part-time",
    "contract",
    "temporary",
    "internship",
    "apprenticeship",
]
THEME_MODES = ["auto", "light", "dark"]

DEFAULT_SETTINGS = {
    "embedding_weight": 0.68,
    "skill_weight": 0.22,
    "experience_weight": 0.10,
    "theme_mode": "auto",
    "scheduler_enabled": False,
    "scheduler_interval_minutes": 180,
    "scheduler_source_ids": [],
    "embedding_model_name": DEFAULT_MODEL_NAME,
    "max_source_jobs": 120,
    "ollama_enabled": False,
    "ollama_base_url": os.getenv("JOBMATCH_OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
    "ollama_model_name": os.getenv("JOBMATCH_OLLAMA_MODEL", "gemma3:12b"),
    "ollama_enhance_resume": True,
    "ollama_enhance_jobs": True,
    "ollama_max_job_enrichments": 20,
}

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0 Safari/537.36 "
        "JobMatchLocal/0.1"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


def ensure_directories() -> None:
    for directory in (DATA_DIR, UPLOADS_DIR, EXPORTS_DIR, LOGS_DIR, BROWSER_PROFILES_DIR):
        directory.mkdir(parents=True, exist_ok=True)
