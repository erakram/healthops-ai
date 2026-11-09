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
    llm_provider: str  # anthropic | groq
    anthropic_api_key: str | None
    groq_api_key: str | None
    extraction_model: str  # reads documents
    review_model: str  # review, triage and appeals
    claude_effort: str  # low | medium | high | xhigh | max (Claude only)
    request_timeout: float  # seconds per LLM request
    max_retries: int  # LLM retries on rate limits, timeouts and 5xx errors
    job_max_attempts: int  # a failed pipeline step is retried this many times
    jwt_secret: str
    session_hours: int
    seed_demo_data: bool  # demo users + sample claim history on first start
    max_upload_mb: float
    payer_delay_seconds: float  # how long the mock payer takes to answer (days, in real life)
    phi_masking: bool  # replace patient identifiers with tokens before any LLM call (core/phi.py)
    hospital_name: str  # printed on OPD slips

    @classmethod
    def from_env(cls) -> "Settings":
        anthropic_key = os.getenv("ANTHROPIC_API_KEY") or None
        groq_key = os.getenv("GROQ_API_KEY") or None
        # Claude when its key is present, unless LLM_PROVIDER says otherwise.
        provider = (os.getenv("LLM_PROVIDER") or ("anthropic" if anthropic_key or not groq_key else "groq")).lower()
        default_model = os.getenv("LLM_MODEL") or (
            "claude-opus-5-5" if provider == "anthropic" else os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"))
        return cls(
            llm_provider=provider,
            anthropic_api_key=anthropic_key,
            claude_effort=os.getenv("CLAUDE_EFFORT", "medium"),
            database_url=os.getenv("DATABASE_URL", f"sqlite:///{BACKEND_ROOT / 'data' / 'healthops.db'}"),
            storage_dir=Path(os.getenv("STORAGE_DIR", BACKEND_ROOT / "data" / "documents")),
            groq_api_key=groq_key,
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
            phi_masking=os.getenv("PHI_MASKING", "true").lower() == "true",
            hospital_name=os.getenv("HOSPITAL_NAME", "HealthOps Demo Hospital"),
        )


    @property
    def llm_api_key(self) -> str | None:
        return self.anthropic_api_key if self.llm_provider == "anthropic" else self.groq_api_key

    @property
    def llm_key_name(self) -> str:
        return "ANTHROPIC_API_KEY" if self.llm_provider == "anthropic" else "GROQ_API_KEY"


settings = Settings.from_env()
