"""Patient billing: statements from patient-responsibility adjustments, payments, plans and reminders."""

from datetime import date, timedelta

from sqlalchemy import select

from app.billing import PLAIN_REASONS, run_reminders
from app.db import SessionLocal
from app.models import Statement
from tests.conftest import login
from tests.test_denials import _denial_of, _submit


def _new_statement(client) -> dict:
    """Bill a patient through the real flow: a deductible on a remittance, then 'bill patient'."""
    claim_id, _ = _submit(client, "deductible")
    denial = _denial_of(client, claim_id)
    client.post(f"/api/denials/{denial['id']}/action", json={"action": "bill_patient"})
    number = next(e["message"].split()[-1] for e in client.get(f"/api/claims/{claim_id}").json()["events"]
                  if "statement STM-" in e["message"])
    return next(r for r in client.get("/api/billing/statements").json() if r["number"] == number)


def test_seeded_statements_have_plain_reasons_and_pdfs(client):
    login(client, "biller")
    rows = client.get("/api/billing/statements", params={"view": "all"}).json()
    assert rows and all(i["reason"] in PLAIN_REASONS.values() for r in rows for i in r["items"])
    r = client.get(f"/api/billing/statements/{rows[0]['id']}/letters/0")
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    summary = client.get("/api/billing/summary").json()
    assert summary["collected"] > 0 and summary["on_plan"] >= 1  # seeded: one paid, one on a plan


def test_payment_plan_and_payments(client):
    login(client, "biller")
    s = _new_statement(client)
    assert s["status"] == "open" and s["items"][0]["code"] == "PR-1"
    assert client.post(f"/api/billing/statements/{s['id']}/payments",
                       json={"amount": s["balance"] + 50, "method": "card"}).status_code == 400  # more than owed
    plan = client.post(f"/api/billing/statements/{s['id']}/plan", json={"installments": 3}).json()
    assert plan["status"] == "plan" and len(plan["plan"]["schedule"]) == 3
    assert round(sum(p["amount"] for p in plan["plan"]["schedule"]), 2) == s["balance"]
    first = plan["plan"]["schedule"][0]["amount"]
    paid = client.post(f"/api/billing/statements/{s['id']}/payments", json={"amount": first, "method": "ach"}).json()
    assert paid["plan"]["schedule"][0]["paid"] and paid["status"] == "plan" and paid["payments"]
    rest = paid["balance"]
    done = client.post(f"/api/billing/statements/{s['id']}/payments", json={"amount": rest, "method": "cash"}).json()
    assert done["status"] == "paid" and done["balance"] == 0


def test_reminder_cycle_moves_unpaid_statements_and_stops_at_review(client):
    number = _new_statement(client)["number"]
    with SessionLocal() as db:
        s = db.scalar(select(Statement).where(Statement.number == number))
        issued = date.fromisoformat(s.issued_on)
        for days, stage in ((31, "reminder"), (61, "final_notice"), (91, "review")):
            run_reminders(db, today=issued + timedelta(days=days))
            db.refresh(s)
            assert s.stage == stage
        assert s.status == "review" and [l["stage"] for l in s.letters][-2:] == ["reminder", "final_notice"]
