"""Patient billing: statements, payments, payment plans and the reminder cycle."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.serializers import iso
from app.billing import record_payment, run_reminders, set_up_plan, summary
from app.core.auth import current_user, require_role
from app.db import get_db
from app.models import PatientPayment, Statement, User

router = APIRouter(prefix="/api/billing", tags=["patient billing"])


def statement_json(s: Statement, payments: list[PatientPayment] | None = None) -> dict:
    out = {"id": s.id, "number": s.number, "patient_name": s.patient_name, "items": s.items, "total": s.total,
           "paid": s.paid, "balance": round(s.total - s.paid, 2), "status": s.status, "stage": s.stage,
           "issued_on": s.issued_on, "due_on": s.due_on, "plan": s.plan,
           "letters": [{"stage": l["stage"], "at": l["at"], "index": i} for i, l in enumerate(s.letters or [])]}
    if payments is not None:
        out["payments"] = [{"amount": p.amount, "method": p.method, "reference": p.reference,
                            "received_on": p.received_on, "recorded_by": p.recorded_by, "at": iso(p.at)} for p in payments]
    return out


@router.get("/statements")
def statements(view: Literal["open", "all"] = "open", db: Session = Depends(get_db), _: User = Depends(current_user)):
    query = select(Statement).order_by(Statement.id.desc())
    if view == "open":
        query = query.where(Statement.status != "paid")
    return [statement_json(s) for s in db.scalars(query)]


@router.get("/summary")
def billing_summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    return summary(db)


def _load(db: Session, statement_id: int) -> Statement:
    s = db.get(Statement, statement_id)
    if not s:
        raise HTTPException(404, "Statement not found")
    return s


@router.get("/statements/{statement_id}")
def statement(statement_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    s = _load(db, statement_id)
    payments = db.scalars(select(PatientPayment).where(PatientPayment.statement_id == s.id).order_by(PatientPayment.id)).all()
    return statement_json(s, payments)


@router.get("/statements/{statement_id}/letters/{index}")
def letter(statement_id: int, index: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    s = _load(db, statement_id)
    if not 0 <= index < len(s.letters or []):
        raise HTTPException(404, "No such letter")
    return FileResponse(s.letters[index]["path"], media_type="application/pdf",
                        filename=f"{s.number}-{s.letters[index]['stage']}.pdf")


class Payment(BaseModel):
    amount: float = Field(gt=0, le=100_000)
    method: Literal["card", "cash", "check", "ach"]
    reference: str | None = Field(default=None, max_length=60)


@router.post("/statements/{statement_id}/payments")
def pay(statement_id: int, body: Payment, db: Session = Depends(get_db), user: User = Depends(require_role("biller", "intake"))):
    s = _load(db, statement_id)
    try:
        record_payment(db, s, body.amount, body.method, body.reference, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return statement(statement_id, db, user)


class Plan(BaseModel):
    installments: int = Field(ge=2, le=12)


@router.post("/statements/{statement_id}/plan")
def plan(statement_id: int, body: Plan, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    s = _load(db, statement_id)
    if s.status not in ("open", "partial", "review"):
        raise HTTPException(409, f"Statement is {s.status}")
    try:
        set_up_plan(db, s, body.installments, user)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return statement(statement_id, db, user)


@router.post("/reminders")
def reminders(db: Session = Depends(get_db), _: User = Depends(require_role("biller"))):
    return {"moved": run_reminders(db)}
