"""Patient billing: statements, reminders, payment plans and payments.

When a remittance leaves part of a claim to the patient (deductible, coinsurance, copay) or
coverage had ended, the biller bills the patient: the amount goes on the patient's open
statement with a plain-language reason taken from the payer's adjustment code. Unpaid
statements move through reminder (30 days) and final notice (60 days) to review (90 days),
where a person decides; nothing is sent to collections automatically. All rules, no model:
the explanations are fixed text per reason code, so they can't be wrong.
"""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.core import audit
from app.models import Claim, Denial, PatientPayment, Statement, User
from app.verticals.healthcare.letters import patient_statement
from app.verticals.healthcare.schema import normalize_id

PLAIN_REASONS = {
    "1": "Your deductible: what you pay each year before your plan starts paying",
    "2": "Your coinsurance: your share of the cost after the deductible",
    "3": "Your copay for this visit",
    "27": "Your coverage had ended before this visit, so your plan didn't pay",
}
STAGES = [(90, "review"), (60, "final_notice"), (30, "reminder")]
PAYMENT_TERMS_DAYS = 30


def _us(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%m/%d/%Y") if iso else ""


def _balance(s: Statement) -> float:
    return round(s.total - s.paid, 2)


def _render(db: Session, s: Statement, stage: str, today: date) -> None:
    folder = settings.storage_dir / "statements"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{s.number}-{stage}.pdf"
    claim_numbers = {i["claim_number"] for i in s.items}
    provider = next((c.extracted.get("claim_form", {}).get("provider_name", {}).get("value")
                     for c in db.scalars(select(Claim).where(Claim.number.in_(claim_numbers)))), None) or "Billing Office"
    plan_text = None
    if s.plan:
        next_due = next((p for p in s.plan["schedule"] if not p["paid"]), None)
        if next_due:
            plan_text = (f"Payment plan: {s.plan['installments']} monthly payments of ${next_due['amount']:,.2f}; "
                         f"next due {_us(next_due['due_on'])}.")
    patient_statement(str(path), {"provider_name": provider, "number": s.number, "stage": stage,
                                  "patient_name": s.patient_name, "issued_on": _us(today.isoformat()),
                                  "due_on": _us(s.due_on), "items": s.items, "total": s.total, "paid": s.paid,
                                  "plan_text": plan_text})
    s.letters = [*(s.letters or []), {"stage": stage, "path": str(path), "at": today.isoformat()}]


def bill_patient(db: Session, denial: Denial, user_email: str, today: date | None = None) -> Statement:
    """Put a patient-responsibility amount on the patient's open statement (or a new one)."""
    today = today or date.today()
    claim = denial.claim
    form = (claim.extracted or {}).get("claim_form", {})
    key = normalize_id((form.get("member_id") or {}).get("value")) or f"CLAIM{claim.id}"
    descriptions = {l.get("cpt"): l.get("description") for l in (form.get("line_items") or {}).get("value", [])}
    item = {"denial_id": denial.id, "claim_number": claim.number, "service_date": _us(claim.service_date),
            "code": f"{denial.group_code}-{denial.reason_code}",
            "description": ", ".join(descriptions.get(l["cpt"]) or l["cpt"] for l in denial.lines) or "Services",
            "reason": PLAIN_REASONS.get(denial.reason_code, "Your share under your plan"), "amount": denial.amount}
    statement = db.scalar(select(Statement).where(Statement.patient_key == key, Statement.status == "open",
                                                  Statement.paid == 0, Statement.stage == "statement"))
    if statement is None:
        statement = Statement(number="pending", patient_key=key, patient_name=claim.patient_name or "Patient",
                              items=[], issued_on=today.isoformat(),
                              due_on=(today + timedelta(days=PAYMENT_TERMS_DAYS)).isoformat())
        db.add(statement)
        db.flush()
        statement.number = f"STM-{60000 + statement.id}"
    statement.items = [*statement.items, item]
    statement.total = round(sum(i["amount"] for i in statement.items), 2)
    statement.letters = [l for l in statement.letters or [] if l["stage"] != "statement"]
    _render(db, statement, "statement", today)
    audit.record(db, user_email, "patient.billed", "statement", statement.number,
                 {"claim": claim.number, "code": item["code"], "amount": item["amount"]}, commit=False)
    db.commit()
    return statement


def record_payment(db: Session, s: Statement, amount: float, method: str, reference: str | None, user: User,
                   today: date | None = None) -> PatientPayment:
    today = today or date.today()
    if amount <= 0 or amount > _balance(s) + 0.005:
        raise ValueError(f"Payment must be between $0.01 and the balance (${_balance(s):,.2f})")
    payment = PatientPayment(statement_id=s.id, amount=round(amount, 2), method=method, reference=reference,
                             received_on=today.isoformat(), recorded_by=user.email)
    db.add(payment)
    s.paid = round(s.paid + amount, 2)
    if s.plan:
        remaining = amount
        schedule = [dict(p) for p in s.plan["schedule"]]
        for p in schedule:
            if not p["paid"] and remaining >= p["amount"] - 0.005:
                p["paid"], remaining = True, round(remaining - p["amount"], 2)
        s.plan = {**s.plan, "schedule": schedule}
    s.status = "paid" if _balance(s) <= 0.005 else ("plan" if s.plan else "partial")
    audit.record(db, user.email, "patient.payment", "statement", s.number,
                 {"amount": amount, "method": method, "balance": _balance(s)}, commit=False)
    db.commit()
    return payment


def set_up_plan(db: Session, s: Statement, installments: int, user: User, today: date | None = None) -> None:
    today = today or date.today()
    balance = _balance(s)
    if not 2 <= installments <= 12 or balance <= 0:
        raise ValueError("A plan needs 2 to 12 monthly payments and a balance")
    each = round(balance / installments, 2)
    amounts = [each] * (installments - 1) + [round(balance - each * (installments - 1), 2)]
    schedule = [{"due_on": (today + timedelta(days=30 * (i + 1))).isoformat(), "amount": a, "paid": False}
                for i, a in enumerate(amounts)]
    s.plan = {"installments": installments, "schedule": schedule, "set_up_on": today.isoformat()}
    s.status, s.stage = "plan", "statement"
    s.due_on = schedule[0]["due_on"]
    _render(db, s, "statement", today)
    audit.record(db, user.email, "patient.plan", "statement", s.number, {"installments": installments, "each": each},
                 commit=False)
    db.commit()


def run_reminders(db: Session, today: date | None = None) -> list[dict]:
    """Move unpaid statements to their next stage. Plans are reminded only for a missed installment."""
    today = today or date.today()
    moved = []
    for s in db.scalars(select(Statement).where(Statement.status.in_(("open", "partial", "plan")))):
        if s.plan:
            overdue = [p for p in s.plan["schedule"] if not p["paid"] and p["due_on"] < today.isoformat()]
            if not overdue or s.stage != "statement":
                continue
            target = "reminder"
        else:
            age = (today - date.fromisoformat(s.issued_on)).days
            target = next((stage for days, stage in STAGES if age >= days), None)
        order = ["statement", "reminder", "final_notice", "review"]
        if target is None or order.index(target) <= order.index(s.stage):
            continue
        s.stage = target
        if target == "review":
            s.status = "review"  # a person decides: financial assistance, another plan, or collections
        else:
            s.due_on = (today + timedelta(days=14 if target == "final_notice" else 21)).isoformat()
            _render(db, s, target, today)
        moved.append({"statement": s.number, "stage": target})
    db.commit()
    return moved


def summary(db: Session, today: date | None = None) -> dict:
    today = today or date.today()
    statements = db.scalars(select(Statement)).all()
    open_ = [s for s in statements if s.status != "paid"]
    collected = round(sum(s.paid for s in statements), 2)
    billed = round(sum(s.total for s in statements), 2)
    return {"statements": len(statements), "open": len(open_), "outstanding": round(sum(_balance(s) for s in open_), 2),
            "collected": collected, "collection_rate": round(100 * collected / billed, 1) if billed else None,
            "on_plan": sum(1 for s in statements if s.status == "plan"),
            "needs_review": sum(1 for s in statements if s.status == "review"),
            "by_stage": {stage: sum(1 for s in open_ if s.stage == stage) for stage in ("statement", "reminder", "final_notice", "review")}}
