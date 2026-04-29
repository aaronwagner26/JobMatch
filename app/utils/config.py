from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_ROOT / "data"
UPLOADS_DIR = DATA_DIR / "uploads"
EXPORTS_DIR = DATA_DIR / "exports"
LOGS_DIR = DATA_DIR / "logs"
DB_PATH = DATA_DIR / "jobmatch.sqlite3"

APP_NAME = "JobMatch"
DEFAULT_MODEL_NAME = os.getenv("JOBMATCH_EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")
DEFAULT_SCAN_CONCURRENCY = 4
DEFAULT_DETAIL_FETCH_LIMIT = 10
DEFAULT_HTTP_TIMEOUT = 25.0

SOURCE_TYPES = [
    "auto",
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

DEFAULT_SETTINGS = {
    "embedding_weight": 0.68,
    "skill_weight": 0.22,
    "experience_weight": 0.10,
    "scheduler_enabled": False,
    "scheduler_interval_minutes": 180,
    "scheduler_source_ids": [],
    "embedding_model_name": DEFAULT_MODEL_NAME,
    "max_source_jobs": 120,
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
    for directory in (DATA_DIR, UPLOADS_DIR, EXPORTS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)

