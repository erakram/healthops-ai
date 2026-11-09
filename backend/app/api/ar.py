"""Accounts receivable: the unpaid-claims worklist, status checks (276/277) and follow-up actions."""

from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ar import ACTIONS, BUCKETS, act, check_status, row_json, unpaid_claims
from app.core.auth import current_user, require_role
from app.db import get_db
from app.models import Claim, FollowUp, Submission, User

router = APIRouter(prefix="/api/ar", tags=["accounts receivable"])


def _rows(db: Session) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    followups = {f.claim_id: f for f in db.scalars(select(FollowUp))}
    return [row_json(claim, first, followups.get(claim.id), today) for claim, first in unpaid_claims(db)]


@router.get("")
def worklist(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return sorted(_rows(db), key=lambda r: (not r["due_for_follow_up"], -r["priority"]))


@router.get("/summary")
def summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    rows = _rows(db)
    buckets = [{"bucket": label, "count": sum(1 for r in rows if r["bucket"] == label),
                "amount": round(sum(r["amount"] for r in rows if r["bucket"] == label), 2)} for _, label in BUCKETS]
    total = sum(r["amount"] for r in rows)
    return {"claims": len(rows), "amount": round(total, 2), "buckets": buckets,
            "due_for_follow_up": sum(1 for r in rows if r["due_for_follow_up"]),
            "filing_risk": sum(1 for r in rows if r["days_to_filing_deadline"] <= 30),
            "average_age_days": round(sum(r["age_days"] * r["amount"] for r in rows) / total, 1) if total else None}


def _load(db: Session, claim_id: int) -> tuple[Claim, Submission]:
    claim = db.get(Claim, claim_id)
    first = db.scalars(select(Submission).where(Submission.claim_id == claim_id).order_by(Submission.id)).first() if claim else None
    if not claim or not first:
        raise HTTPException(404, "No submitted claim with that id")
    return claim, first


@router.get("/{claim_id}")
def detail(claim_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    claim, first = _load(db, claim_id)
    followup = db.scalar(select(FollowUp).where(FollowUp.claim_id == claim_id))
    return {**row_json(claim, first, followup, datetime.now(timezone.utc).date()), "claim_status": claim.status,
            "original_reference": first.payer_reference, "checks": followup.checks if followup else [],
            "action_taken": followup.action_taken if followup else None, "actions": ACTIONS}


@router.post("/{claim_id}/check")
def status_check(claim_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    claim, _ = _load(db, claim_id)
    if claim.status != "submitted":
        raise HTTPException(409, f"Claim is {claim.status}; the payer has already answered")
    check_status(db, claim, user, getattr(request.app.state, "worker").llm if hasattr(request.app.state, "worker") else None)
    return detail(claim_id, db, user)


class Action(BaseModel):
    action: Literal["resubmit", "send_records", "request_remittance", "wait"]
    note: str | None = Field(default=None, max_length=500)


@router.post("/{claim_id}/action")
def follow_up(claim_id: int, body: Action, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    claim, _ = _load(db, claim_id)
    if claim.status != "submitted":
        raise HTTPException(409, f"Claim is {claim.status}; nothing to follow up")
    try:
        act(db, claim, user, body.action, body.note)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    return detail(claim_id, db, user)
