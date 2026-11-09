"""HealthOps AI API.

    uvicorn app.main:app --reload --port 8010

On start: apply database migrations, seed demo users and history (if SEED_DEMO_DATA), and start
the background worker that runs the claim pipeline.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import ar, auth, authorizations, billing, claims, coding, denials, frontdesk, insights, reconciliation
from app.config import settings
from app.core.llm import default_llm
from app.db import SessionLocal, engine
from app.db_migrate import migrate
from app.seed import seed_history, seed_opd, seed_users
from app.worker import Worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite"):
        settings.storage_dir.parent.mkdir(parents=True, exist_ok=True)
    migrate(engine)
    if settings.seed_demo_data:
        with SessionLocal() as db:
            seed_users(db)
            seed_history(db)
            seed_opd(db)
    worker = Worker(app.state.llm if hasattr(app.state, "llm") else default_llm())
    worker.start()
    app.state.worker = worker
    yield
    worker.stop()


app = FastAPI(title="HealthOps AI", version="0.1.0", lifespan=lifespan)
app.include_router(auth.router)
app.include_router(claims.router)
app.include_router(insights.router)
app.include_router(denials.router)
app.include_router(authorizations.router)
app.include_router(coding.router)
app.include_router(ar.router)
app.include_router(billing.router)
app.include_router(reconciliation.router)
app.include_router(frontdesk.router)


@app.get("/api/health")
def health():
    return {"ok": True, "llm_configured": settings.llm_api_key is not None,
            "provider": settings.llm_provider, "model": settings.review_model, "phi_masking": settings.phi_masking}
