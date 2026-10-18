"""Central configuration.

All paths resolve relative to this file so the app can be launched from any
working directory (uvicorn, pytest, scripts).
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings

BACKEND_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = BACKEND_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
EXPORTS_DIR = DATA_DIR / "exports"
WAREHOUSE_DIR = DATA_DIR / "warehouse"
DB_PATH = WAREHOUSE_DIR / "platform.db"

for _dir in (RAW_DIR, EXPORTS_DIR, WAREHOUSE_DIR):
    _dir.mkdir(parents=True, exist_ok=True)


class Settings(BaseSettings):
    """Runtime settings, overridable via environment variables."""

    # These two intentionally use the plain (unprefixed) env var names since
    # ANTHROPIC_API_KEY is the conventional name used across the ecosystem.
    anthropic_api_key: str | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    claude_model: str = Field(default="claude-opus-5-5", validation_alias="CLAUDE_MODEL")
    # Set PLATFORM_DATABASE_URL (env var or .env) to use PostgreSQL, e.g.
    # postgresql+psycopg2://user:password@host:5432/dbname
    database_url: str = f"sqlite:///{DB_PATH.as_posix()}"
    max_upload_mb: int = 50
    request_timeout_seconds: int = 30
    max_profile_sample_rows: int = 20
    job_workers: int = 2
    # Delete a downloaded .xls/.csv once its rows are committed; the database
    # is the record from then on, and an HMIS crawl otherwise leaves gigabytes
    # on disk. The .meta.json sidecar is kept either way, so a purged file
    # still says which version of the report produced its rows.
    # Set PLATFORM_DELETE_SOURCE_FILES_AFTER_INGEST=false to keep the files —
    # rebuild_hmis_raw.py needs them to rebuild hmis_raw without re-downloading.
    delete_source_files_after_ingest: bool = True

    model_config = {
        "env_prefix": "PLATFORM_",
        "env_file": BACKEND_DIR / ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()
