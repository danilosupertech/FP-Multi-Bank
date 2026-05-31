"""Central project paths used by importers, parsers, scripts and dashboard.

The import flow intentionally uses one single input folder: ``data/raw``.
Bank/type detection is done by code, not by bank-specific folders.
"""

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"

RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
FAILED_DIR = DATA_DIR / "failed"
STORAGE_DIR = DATA_DIR / "storage"
RULES_DIR = DATA_DIR / "rules"
CACHE_DIR = DATA_DIR / "cache"
LOGS_DIR = DATA_DIR / "logs"

DB_PATH = STORAGE_DIR / "financial.db"


def ensure_data_directories() -> None:
    """Create the project data folders expected by import and dashboard flows."""
    for directory in (RAW_DIR, PROCESSED_DIR, FAILED_DIR, STORAGE_DIR, RULES_DIR, CACHE_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)
