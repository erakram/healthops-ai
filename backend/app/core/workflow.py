"""The claim lifecycle as an explicit state machine.

    received -> extracting -> validating -> needs_review | ready
    needs_review | ready -> approved | rejected      (a person decides; agents never do)
    needs_review | ready -> validating              (after a reviewer corrects a field)
    approved -> submitted                            (sent to the payer)
    submitted -> paid | denied                       (the payer's 835 arrives)
    denied -> appealed -> paid | denied              (appeal overturned or upheld)
    denied -> submitted                              (corrected claim sent)
    denied -> paid | closed                          (balance billed or written off)
    any agent step -> failed -> extracting           (retry)

Each step of the pipeline is a Job. Jobs are claimed from the database, so a claim
waiting days for approval costs nothing, and a crash mid-step only means the job is
picked up again.
"""

from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit, events
from app.models import Claim, ClaimEvent, Job


class Status(StrEnum):
    RECEIVED = "received"
    EXTRACTING = "extracting"
    VALIDATING = "validating"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    APPROVED = "approved"
    REJECTED = "rejected"
    SUBMITTED = "submitted"
    PAID = "paid"
    DENIED = "denied"
    APPEALED = "appealed"
    CLOSED = "closed"
    FAILED = "failed"


TRANSITIONS: dict[Status, set[Status]] = {
    Status.RECEIVED: {Status.EXTRACTING, Status.FAILED},
    Status.EXTRACTING: {Status.VALIDATING, Status.FAILED},
    Status.VALIDATING: {Status.NEEDS_REVIEW, Status.READY, Status.FAILED},
    Status.NEEDS_REVIEW: {Status.APPROVED, Status.REJECTED, Status.VALIDATING},
    Status.READY: {Status.APPROVED, Status.REJECTED, Status.VALIDATING},
    Status.APPROVED: {Status.SUBMITTED, Status.FAILED},
    Status.FAILED: {Status.EXTRACTING, Status.VALIDATING, Status.APPROVED},
    Status.REJECTED: set(),
    Status.SUBMITTED: {Status.PAID, Status.DENIED, Status.CLOSED},
    Status.DENIED: {Status.APPEALED, Status.SUBMITTED, Status.PAID, Status.CLOSED},
    Status.APPEALED: {Status.PAID, Status.DENIED, Status.CLOSED},
    Status.PAID: {Status.DENIED},  # a later remittance can take a payment back
    Status.CLOSED: set(),
}


class DenialStatus(StrEnum):
    TRIAGE = "triage"  # agents are analysing it
    OPEN = "open"  # waiting for a person to act on the recommendation
    APPEAL_SENT = "appeal_sent"
    RESUBMITTED = "resubmitted"  # corrected claim sent
    UPHELD = "upheld"  # appeal lost: write off or escalate
    OVERTURNED = "overturned"
    PAID_ON_RESUBMISSION = "paid_on_resubmission"
    PATIENT_BILLED = "patient_billed"
    WRITTEN_OFF = "written_off"
    SUPERSEDED = "superseded"  # replaced by the remittance for a corrected claim


DENIAL_OPEN = {DenialStatus.TRIAGE, DenialStatus.OPEN, DenialStatus.UPHELD}
DENIAL_WAITING = {DenialStatus.APPEAL_SENT, DenialStatus.RESUBMITTED}

AWAITING_HUMAN = {Status.NEEDS_REVIEW, Status.READY}


class InvalidTransition(Exception):
    pass


def transition(db: Session, claim: Claim, to: Status, actor: str, note: str | None = None) -> None:
    current = Status(claim.status)
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"Claim {claim.number} can't go from {current} to {to}")
    claim.status = to
    db.add(ClaimEvent(claim_id=claim.id, type="transition", actor=actor,
                      message=note or f"Moved to {to.replace('_', ' ')}", data={"from": current, "to": to}))
    audit.record(db, actor, "claim.transition", "claim", claim.number,
                 {"from": current, "to": to, "note": note}, commit=False)
    db.commit()
    events.publish("claim.updated", claim_id=claim.id, status=to)


def log_step(db: Session, claim: Claim, agent: str, message: str, **data) -> None:
    """An agent's progress message, shown live on the claim page."""
    db.add(ClaimEvent(claim_id=claim.id, type="step", actor=agent, message=message, data=data))
    db.commit()
    events.publish("claim.step", claim_id=claim.id, agent=agent, message=message)


def enqueue(db: Session, kind: str, claim: Claim, key: str | None = None, payload: dict | None = None,
            delay_seconds: float = 0) -> Job | None:
    """Queue a pipeline step. Returns None if a job with this key already exists."""
    job = Job(kind=kind, claim_id=claim.id, idempotency_key=key or f"{kind}:{claim.id}", payload=payload or {},
              run_after=datetime.now(timezone.utc) + timedelta(seconds=delay_seconds))
    db.add(job)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        return None
    return job


def claim_next_job(db: Session) -> Job | None:
    """Take the oldest due job. The conditional UPDATE means two workers can't take the same one."""
    now = datetime.now(timezone.utc)
    candidates = db.scalars(select(Job.id).where(Job.status == "queued", Job.run_after <= now)
                            .order_by(Job.id).limit(5)).all()
    for job_id in candidates:
        taken = db.execute(update(Job).where(Job.id == job_id, Job.status == "queued")
                           .values(status="running", attempts=Job.attempts + 1))
        db.commit()
        if taken.rowcount == 1:
            return db.get(Job, job_id)
    return None


def finish_job(db: Session, job: Job) -> None:
    job.status = "done"
    db.commit()


def defer_job(db: Session, job: Job, seconds: float, reason: str) -> None:
    """Put a job back without counting the attempt: used when the provider asks us to wait."""
    job.status = "queued"
    job.attempts = max(job.attempts - 1, 0)
    job.last_error = reason[:2000]
    job.run_after = datetime.now(timezone.utc) + timedelta(seconds=seconds)
    db.commit()


def fail_job(db: Session, job: Job, error: str, max_attempts: int) -> bool:
    """Record a failure. Returns True if the job will be retried (with backoff)."""
    job.last_error = error[:2000]
    if job.attempts < max_attempts:
        job.status = "queued"
        job.run_after = datetime.now(timezone.utc) + timedelta(seconds=2 ** job.attempts)
        db.commit()
        return True
    job.status = "failed"
    db.commit()
    return False
