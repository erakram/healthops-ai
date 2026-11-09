"""Bank reconciliation: does the money the payers said they sent match what reached the bank?

Payers pay by EFT and send an 835; the bank deposit carries the same trace number in its
ACH addenda (TRN*1*<trace>), which is how the two are tied (the "reassociation" step).
Each deposit is matched by trace number, or else by amount within a few days. Results:
  matched          deposit and 835 agree
  amount_differs   same trace, different amount (fees, recoupments)
  unmatched        money with no 835 (ask the payer for the remittance; A/R may know the claim)
and 835s whose money never arrived are listed for follow-up with the payer or bank.
All rules: this is accounting, and it has to add up exactly.
"""

import csv
import io
import re
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import BankDeposit, Claim, FollowUp, Remittance

_TRACE = re.compile(r"TRN\*1\*([A-Z0-9-]+)")
CSV_COLUMNS = ["posted_on", "amount", "description", "bank_reference"]


def parse_statement(text: str) -> list[dict]:
    """A bank statement CSV with columns posted_on (YYYY-MM-DD), amount, description, bank_reference."""
    rows = list(csv.DictReader(io.StringIO(text.strip())))
    if not rows or any(c not in rows[0] for c in CSV_COLUMNS):
        raise ValueError(f"Expected CSV columns: {', '.join(CSV_COLUMNS)}")
    out = []
    for r in rows:
        try:
            amount = round(float(r["amount"]), 2)
            date.fromisoformat(r["posted_on"])
        except ValueError as exc:
            raise ValueError(f"Bad row {r}: {exc}") from exc
        match = _TRACE.search(r["description"] or "")
        out.append({"posted_on": r["posted_on"], "amount": amount, "description": r["description"],
                    "bank_reference": r["bank_reference"], "trace_number": match.group(1) if match else None})
    return out


def import_statement(db: Session, text: str) -> dict:
    added = skipped = 0
    for row in parse_statement(text):
        if row["amount"] <= 0:
            continue  # withdrawals aren't payer deposits
        db.add(BankDeposit(**row))
        try:
            db.commit()
            added += 1
        except IntegrityError:
            db.rollback()
            skipped += 1  # already imported
    return {"added": added, "already_imported": skipped, **reconcile(db)}


def _eft_remittances(db: Session) -> dict[str, list[Remittance]]:
    """Remittances that carried money, grouped by trace number (one EFT per trace)."""
    by_trace: dict[str, list[Remittance]] = {}
    for r in db.scalars(select(Remittance)):
        if (r.parsed.get("payment") or {}).get("amount", 0) > 0:
            by_trace.setdefault(r.trace_number, []).append(r)
    return by_trace


def reconcile(db: Session) -> dict:
    by_trace = _eft_remittances(db)
    used: set[str] = set()
    counts = {"matched": 0, "amount_differs": 0, "unmatched": 0}
    for d in db.scalars(select(BankDeposit).where(BankDeposit.status != "resolved")):
        remits = by_trace.get(d.trace_number or "")
        if not remits:  # no trace: try amount within 5 days
            for trace, rs in by_trace.items():
                paid_on = (rs[0].parsed.get("payment") or {}).get("date")
                if trace not in used and paid_on and abs(rs[0].parsed["payment"]["amount"] - d.amount) < 0.005 and \
                        abs((date.fromisoformat(d.posted_on) - date.fromisoformat(paid_on)).days) <= 5:
                    remits, d.trace_number = rs, trace
                    break
        if remits:
            expected = round(remits[0].parsed["payment"]["amount"], 2)
            used.add(remits[0].trace_number)
            d.remittance_ids = [r.id for r in remits]
            d.status = "matched" if abs(expected - d.amount) < 0.005 else "amount_differs"
            d.note = None if d.status == "matched" else (
                f"The 835 says ${expected:,.2f}; the bank received ${d.amount:,.2f} "
                f"(difference ${d.amount - expected:,.2f}). Check for a payer offset or a bank fee.")
        else:
            d.remittance_ids, d.status = [], "unmatched"
            d.note = _explain_unmatched(db, d)
        counts[d.status] += 1
    db.commit()
    missing = [{"trace_number": t, "amount": rs[0].parsed["payment"]["amount"], "paid_on": rs[0].parsed["payment"].get("date"),
                "payer": rs[0].payer_name, "claims": [r.claim_id for r in rs]}
               for t, rs in by_trace.items() if t not in used and not _recent(rs[0])]
    return {**counts, "remittances_without_deposit": len(missing), "missing_deposits": missing}


def _recent(remit: Remittance) -> bool:
    """EFTs settle in 1-3 business days: don't flag a missing deposit yet."""
    paid_on = (remit.parsed.get("payment") or {}).get("date")
    return bool(paid_on) and date.fromisoformat(paid_on) >= date.today() - timedelta(days=3)


def _explain_unmatched(db: Session, d: BankDeposit) -> str:
    """Money with no 835: if an A/R status check found a payment of this amount, name the claim."""
    for f in db.scalars(select(FollowUp)):
        last = (f.checks or [{}])[-1].get("status") or {}
        if last.get("paid_amount") is not None and abs(last["paid_amount"] - d.amount) < 0.005:
            claim = db.get(Claim, f.claim_id)
            return (f"No 835 for this deposit. The payer's status response for {claim.number} reported a payment of "
                    f"${d.amount:,.2f} (check/EFT {last.get('check_number')}): request that claim's remittance.")
    return "No 835 for this deposit: ask the payer for the remittance advice before posting it."


def bank_statement_for_demo(db: Session, today: date) -> str:
    """A synthetic bank statement for the seeded history: most EFTs arrive as sent, one is short
    by a recoupment, one never arrives, and the payers' paid-but-no-835 claims show up as deposits."""
    from app.verticals.healthcare import payer
    lines = []
    remits = [rs[0] for rs in _eft_remittances(db).values()]
    remits.sort(key=lambda r: r.id)
    for i, r in enumerate(remits):
        payment = r.parsed["payment"]
        amount = payment["amount"]
        if i == 1:
            amount = round(amount - 12.50, 2)  # payer recouped an earlier overpayment from this EFT
        if i == 3:
            continue  # this EFT never reached the bank
        posted = date.fromisoformat(payment["date"]) + timedelta(days=1)
        if posted > today:
            continue
        lines.append([posted.isoformat(), f"{amount:.2f}",
                      f"{r.payer_name} HCCLAIMPMT CCD+ TRN*1*{r.trace_number}*1512345678\\", f"BNK{r.id:08d}"])
    for claim in db.scalars(select(Claim).where(Claim.status == "submitted")):  # paid, no 835 sent
        member = ((claim.extracted or {}).get("claim_form", {}).get("member_id") or {}).get("value")
        if payer.ar_scenario(member) == "paid_no_835":
            amount = round((claim.total_charge or 0) * payer.profile_for(claim.payer_name).allowed_pct, 2)
            lines.append([(today - timedelta(days=11)).isoformat(), f"{amount:.2f}",
                          f"{claim.payer_name} HCCLAIMPMT CCD+ TRN*1*EFT{claim.id:07d}*1512345678\\", f"BNKX{claim.id:07d}"])
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(CSV_COLUMNS)
    writer.writerows(lines)
    return out.getvalue()
