"""Background worker: takes queued jobs from the database and runs them.

It runs as a thread inside the API process so the demo is one command. Because jobs
live in the database and are claimed with a conditional UPDATE, the same loop can run
as separate worker processes in production without changes.
"""

import logging
import threading
import traceback
from datetime import datetime, timedelta, timezone

from sqlalchemy import update

from app.config import settings
from app.core.llm import LLM, RateLimited
from app.core.workflow import (TRANSITIONS, Status, claim_next_job, defer_job, fail_job, finish_job, log_step,
                               transition)
from app.db import SessionLocal
from app.models import Authorization, Claim, Encounter, Job
from app.pipeline import HANDLERS
from app.authorizations import give_up as auth_give_up
from app.authorizations import log_step as auth_log
from app.coding import give_up as enc_give_up
from app.coding import log_step as enc_log
from app.revenue import give_up

logger = logging.getLogger(__name__)


def run_one(llm: LLM | None) -> bool:
    """Run the next due job. Returns False if there was nothing to do."""
    with SessionLocal() as db:
        job = claim_next_job(db)
        if not job:
            return False
        claim = db.get(Claim, job.claim_id) if job.claim_id else None
        try:
            HANDLERS[job.kind](db, claim, llm, job.payload or {})
            finish_job(db, job)
        except RateLimited as exc:
            db.rollback()
            defer_job(db, job, exc.wait_seconds, str(exc))
            _note(db, job, f"Model provider rate limit reached, resuming in {exc.wait_seconds:.0f}s")
        except Exception as exc:  # any failure is recorded on the job and its claim or authorization
            db.rollback()
            logger.warning("Job %s (%s) failed: %s", job.id, job.kind, exc)
            logger.debug(traceback.format_exc())
            message = f"{type(exc).__name__}: {exc}"
            retrying = fail_job(db, job, message, settings.job_max_attempts)
            if retrying:
                _note(db, job, f"Step '{job.kind}' failed, retrying ({message[:160]})")
            elif job.kind.startswith("auth_"):
                auth_give_up(db, job.payload or {}, message)
            elif job.kind.startswith("enc_"):
                enc_give_up(db, job.payload or {}, message)
            elif job.kind in ("triage", "draft_appeal"):
                give_up(db, job.kind, job.payload or {}, message)
                _note(db, job, f"Step '{job.kind}' failed: {message[:300]}")
            else:
                claim = db.get(Claim, job.claim_id)
                if Status.FAILED in TRANSITIONS[Status(claim.status)]:
                    transition(db, claim, Status.FAILED, "Workflow Engine", f"Step '{job.kind}' failed: {message[:300]}")
        return True


def _note(db, job: Job, message: str) -> None:
    """A progress note on whatever the job is about."""
    if job.claim_id:
        log_step(db, db.get(Claim, job.claim_id), "Workflow Engine", message)
    elif (job.payload or {}).get("encounter_id"):
        enc = db.get(Encounter, job.payload["encounter_id"])
        if enc:
            enc_log(db, enc, "Workflow Engine", message)
    elif (job.payload or {}).get("authorization_id"):
        auth = db.get(Authorization, job.payload["authorization_id"])
        if auth:
            auth_log(db, auth, "Workflow Engine", message)


def recover_stuck_jobs() -> None:
    """Jobs left 'running' by a crash or restart go back in the queue."""
    with SessionLocal() as db:
        db.execute(update(Job).where(Job.status == "running")
                   .values(status="queued", run_after=datetime.now(timezone.utc) - timedelta(seconds=1)))
        db.commit()


class Worker:
    def __init__(self, llm: LLM | None, poll_seconds: float = 0.5):
        self.llm = llm
        self.poll_seconds = poll_seconds
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, name="healthops-worker", daemon=True)

    def start(self) -> None:
        recover_stuck_jobs()
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                busy = run_one(self.llm)
            except Exception:
                logger.exception("Worker loop error")
                busy = False
            if not busy:
                self._stop.wait(self.poll_seconds)
