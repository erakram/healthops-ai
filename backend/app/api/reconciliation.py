"""Bank reconciliation: import a bank statement, match deposits to 835 remittances."""

from datetime import date

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core import audit
from app.core.auth import current_user, require_role
from app.db import get_db
from app.models import BankDeposit, Remittance, User
from app.reconciliation import bank_statement_for_demo, import_statement, reconcile

router = APIRouter(prefix="/api/reconciliation", tags=["reconciliation"])


def _deposit(d: BankDeposit, remits: dict[int, Remittance]) -> dict:
    linked = [remits[i] for i in d.remittance_ids if i in remits]
    return {"id": d.id, "posted_on": d.posted_on, "amount": d.amount, "description": d.description,
            "bank_reference": d.bank_reference, "trace_number": d.trace_number, "status": d.status, "note": d.note,
            "remittances": [{"id": r.id, "claim_id": r.claim_id, "payer": r.payer_name,
                             "amount": r.parsed["payment"]["amount"]} for r in linked]}


@router.get("")
def overview(db: Session = Depends(get_db), _: User = Depends(current_user)):
    result = reconcile(db)
    remits = {r.id: r for r in db.scalars(select(Remittance))}
    deposits = db.scalars(select(BankDeposit).order_by(BankDeposit.posted_on.desc(), BankDeposit.id.desc())).all()
    return {**result, "deposits": [_deposit(d, remits) for d in deposits],
            "deposited": round(sum(d.amount for d in deposits), 2)}


@router.post("/import")
async def import_csv(file: UploadFile = File(...), db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    data = await file.read()
    if len(data) > 2 * 1024 * 1024:
        raise HTTPException(400, "Bank statements over 2 MB aren't supported here")
    try:
        result = import_statement(db, data.decode("utf-8", errors="replace"))
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    audit.record(db, user.email, "bank.imported", "file", file.filename or "statement.csv",
                 {k: result[k] for k in ("added", "already_imported", "matched", "amount_differs", "unmatched")})
    return result


@router.get("/demo-statement.csv", response_class=PlainTextResponse)
def demo_statement(db: Session = Depends(get_db), _: User = Depends(current_user)):
    """A synthetic bank statement for the demo data (download it, or load it with the endpoint below)."""
    return bank_statement_for_demo(db, date.today())


@router.post("/demo-statement")
def load_demo_statement(db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    result = import_statement(db, bank_statement_for_demo(db, date.today()))
    audit.record(db, user.email, "bank.imported", "file", "demo-statement.csv",
                 {k: result[k] for k in ("added", "matched", "amount_differs", "unmatched")})
    return result


class Resolve(BaseModel):
    note: str = Field(min_length=1, max_length=500)


@router.post("/deposits/{deposit_id}/resolve")
def resolve(deposit_id: int, body: Resolve, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    d = db.get(BankDeposit, deposit_id)
    if not d or d.status == "matched":
        raise HTTPException(409, "Only exceptions can be resolved")
    d.status, d.note = "resolved", f"{d.note or ''} Resolved by {user.email}: {body.note}".strip()
    audit.record(db, user.email, "bank.resolved", "deposit", d.bank_reference, {"note": body.note}, commit=False)
    db.commit()
    return {"ok": True}
