"""Settings, read from environment variables (or a local .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BACKEND_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    database_url: str  # sqlite locally, postgres in docker / on Render
    storage_dir: Path  # uploaded documents live here
    groq_api_key: str | None
    extraction_model: str  # reads documents: fast and cheap is fine
    review_model: str  # looks for inconsistencies: benefits from a stronger model
    request_timeout: float  # seconds per LLM request
    max_retries: int  # LLM retries on rate limits, timeouts and 5xx errors
    job_max_attempts: int  # a failed pipeline step is retried this many times
    jwt_secret: str
    session_hours: int
    seed_demo_data: bool  # demo users + sample claim history on first start
    max_upload_mb: float
    payer_delay_seconds: float  # how long the mock payer takes to answer (days, in real life)

    @classmethod
    def from_env(cls) -> "Settings":
        default_model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
        return cls(
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_ROOT / 'data' / 'healthops.db'}"),
            storage_dir=Path(os.getenv("STORAGE_DIR", BACKEND_ROOT / "data" / "documents")),
            groq_api_key=os.getenv("GROQ_API_KEY") or None,
            extraction_model=os.getenv("EXTRACTION_MODEL", default_model),
            review_model=os.getenv("REVIEW_MODEL", default_model),
            request_timeout=float(os.getenv("REQUEST_TIMEOUT", "60")),
            max_retries=int(os.getenv("MAX_RETRIES", "3")),
            job_max_attempts=int(os.getenv("JOB_MAX_ATTEMPTS", "3")),
            jwt_secret=os.getenv("JWT_SECRET", "dev-only-secret-change-me-before-deploying!!"),
            session_hours=int(os.getenv("SESSION_HOURS", "12")),
            seed_demo_data=os.getenv("SEED_DEMO_DATA", "true").lower() == "true",
            max_upload_mb=float(os.getenv("MAX_UPLOAD_MB", "10")),
            payer_delay_seconds=float(os.getenv("PAYER_DELAY_SECONDS", "6")),
        )


settings = Settings.from_env()
