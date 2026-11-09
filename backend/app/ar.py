"""Accounts receivable follow-up: claims the payer hasn't answered.

A claim that's been with the payer for a while with no 835 goes on the A/R worklist, aged
into buckets (0-30, 31-60, 61-90, 90+ days) and ranked by money at stake and how close its
timely filing deadline is. A status check sends a 276 and reads the payer's 277; the A/R
Follow-up Agent maps the status to an action with rules, and drafts the message to the
payer (fact-checked like the other letters; a template is used if the draft fails the check).
"""

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.cited_writing import facts_text, norm, unsupported_facts
from app.config import settings
from app.core import audit, events
from app.core.llm import LLM, LLMError
from app.core.workflow import enqueue, log_step
from app.models import AgentRun, Claim, FollowUp, Remittance, Submission, User
from app.verticals.healthcare import payer
from app.verticals.healthcare.prompts import AR_NOTE_SYSTEM
from app.verticals.healthcare.x12_276_277 import build_276, build_277, parse_277

AGENT = "A/R Follow-up Agent"
BUCKETS = [(30, "0-30"), (60, "31-60"), (90, "61-90"), (10**6, "90+")]
FOLLOW_UP_AFTER_DAYS = 14  # payers answer clean electronic claims within about two weeks

ACTIONS = {
    "resubmit": "Resubmit the claim",
    "send_records": "Send the medical records",
    "request_remittance": "Request the remittance (835) copy",
    "wait": "Wait and check again",
    "correct": "Correct and resubmit",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def bucket(age_days: int) -> str:
    return next(label for limit, label in BUCKETS if age_days <= limit)


def unpaid_claims(db: Session) -> list[tuple[Claim, Submission]]:
    """Claims with the payer whose latest submission has no 835 yet."""
    rows = []
    for claim in db.scalars(select(Claim).where(Claim.status == "submitted")):
        first = db.scalars(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.id)).first()
        if first and not db.scalar(select(func.count(Remittance.id)).where(Remittance.claim_id == claim.id)):
            rows.append((claim, first))
    return rows


def row_json(claim: Claim, first: Submission, followup: FollowUp | None, today: date) -> dict:
    profile = payer.profile_for(claim.payer_name)
    sent = _aware(first.at).date()
    age = (today - sent).days
    dos = date.fromisoformat(claim.service_date) if claim.service_date else sent
    deadline = dos + timedelta(days=profile.timely_filing_days)
    days_to_deadline = (deadline - today).days
    amount = claim.total_charge or 0
    urgency = 3 if days_to_deadline <= 30 else 1.5 if days_to_deadline <= 60 else 1
    last = (followup.checks[-1] if followup and followup.checks else None)
    return {
        "claim_id": claim.id, "claim_number": claim.number, "patient_name": claim.patient_name,
        "payer_name": claim.payer_name, "service_date": claim.service_date, "amount": amount,
        "submitted_on": sent.isoformat(), "age_days": age, "bucket": bucket(age),
        "due_for_follow_up": age >= FOLLOW_UP_AFTER_DAYS and (not followup or not followup.next_check_on
                                                             or followup.next_check_on <= today.isoformat()),
        "filing_deadline": deadline.isoformat(), "days_to_filing_deadline": days_to_deadline,
        "priority": round(amount * (1 + age / 30) * urgency),
        "followup_status": followup.status if followup else None,
        "recommended_action": followup.recommended_action if followup else None,
        "last_status": last["status"] if last else None, "next_check_on": followup.next_check_on if followup else None,
    }


def _request(claim: Claim, submission: Submission, trace: str) -> dict:
    p = submission.payload
    profile = payer.profile_for(claim.payer_name)
    return {"claim_number": claim.number, "payer_name": profile.name, "payer_id": profile.id,
            "provider_name": p.get("provider_name") or "Provider", "provider_npi": p.get("provider_npi") or "",
            "patient_last": p.get("patient_last") or "", "patient_first": p.get("patient_first") or "",
            "member_id": p.get("member_id") or "", "charge": float(claim.total_charge or 0),
            "service_date": claim.service_date or _aware(submission.at).date().isoformat(), "trace": trace}


def recommend(status: dict, row: dict) -> tuple[str, list[str], int | None]:
    """Status -> (action, reasons, days until the next check)."""
    category = status["category"]
    if category == "D0":
        return "resubmit", [
            f"The payer has no record of claim {row['claim_number']} ({status['code_text'].lower()}).",
            f"It was first sent on {row['submitted_on']}; resubmit now with that date as proof of timely filing "
            f"(deadline {row['filing_deadline']}, {row['days_to_filing_deadline']} days left)."], 14
    if category == "P3":
        return "send_records", [f"The payer pended the claim: {status['code_text'].lower()}.",
                                f"Payer note: {status['message']}." if status.get("message") else "",
                                "Send the visit note now; the claim is decided once records arrive."], 21
    if category in ("F1", "F2"):
        paid = f"${status['paid_amount']:,.2f}" if status.get("paid_amount") is not None else "a payment"
        return "request_remittance", [
            f"The payer finalized the claim{' and paid ' + paid if category == 'F1' else ' as denied'}"
            + (f" (check/EFT {status['check_number']})" if status.get("check_number") else "") + ", but no 835 arrived.",
            "Request a copy of the remittance so the payment can be posted, and match it to the bank deposit."], 10
    if category == "A7":
        return "correct", [f"The payer rejected the claim: {status['code_text'].lower()}.",
                           "Correct the claim and resubmit it."], 7
    return "wait", [f"The claim is {status['category_text'].lower()}.",
                    f"Payer note: {status['message']}." if status.get("message") else "",
                    "Nothing to do yet; check again in 15 days."], 15


def _template(action: str, facts: dict) -> dict:
    head = f"Claim {facts['claim_number']} for {facts['patient']}, date of service {facts['service_date']}"
    text = {
        "resubmit": f"{head} has no record on file at {facts['payer']}. We are resubmitting it today. The claim was first "
                    f"submitted on {facts['first_submitted_on']} under reference {facts['original_reference']}, within "
                    "the timely filing limit; please process it under the original receipt date.",
        "send_records": f"{head} was pended for medical records. The visit note is attached. Please complete the review "
                        "and process the claim.",
        "request_remittance": f"{head} shows as finalized, but we have not received the electronic remittance (835). "
                              "Please resend the remittance advice so we can post the payment.",
        "correct": f"{head} was rejected for invalid information. A corrected claim follows.",
        "wait": f"{head} is in process. We will check its status again in 15 days.",
    }[action]
    return {"channel": "portal message", "subject": f"{ACTIONS[action]}: {facts['claim_number']}", "message": text,
            "drafted_by": "template"}


def draft_note(llm: LLM | None, action: str, facts: dict) -> tuple[dict, object | None]:
    if llm is None or action == "wait":
        return _template(action, facts), None
    import json
    try:
        result = llm.complete_json(AR_NOTE_SYSTEM, json.dumps({"action": ACTIONS[action], "facts": facts}, indent=1),
                                   model=settings.review_model)
    except LLMError:
        return _template(action, facts), None
    note = {k: str(result.data.get(k) or "") for k in ("channel", "subject", "message")}
    known = facts_text(facts)
    invented = unsupported_facts(note["message"] + " " + note["subject"], norm(known),
                                 {norm(t) for t in known.split()})
    if not note["message"] or invented:
        return {**_template(action, facts), "rejected_draft": note, "rejected_because": invented}, result
    return {**note, "drafted_by": "agent"}, result


def check_status(db: Session, claim: Claim, user: User, llm: LLM | None) -> FollowUp:
    """Send a 276, read the 277, recommend and draft the follow-up."""
    first = db.scalars(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.id)).first()
    latest = db.scalars(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.id.desc())).first()
    followup = db.scalar(select(FollowUp).where(FollowUp.claim_id == claim.id)) or FollowUp(claim_id=claim.id, checks=[])
    now = datetime.now(timezone.utc)
    trace = f"{claim.number}-{len(followup.checks or []) + 1}"
    request = _request(claim, latest, trace)
    profile = payer.profile_for(claim.payer_name)
    x276 = build_276(request, "HEALTHOPS", profile.id, claim.id * 100 + len(followup.checks or []) + 1, now)
    answer = payer.claim_status(claim.payer_name, request, now.date())       # payer side
    x277 = build_277(request, answer, profile.id, "HEALTHOPS", claim.id * 100 + 50, now)
    parsed = parse_277(x277)                                                  # provider side reads the 277
    status = parsed["statuses"][0]
    row = row_json(claim, first, followup, now.date())
    action, reasons, recheck_days = recommend(status, row)
    facts = {"claim_number": claim.number, "patient": claim.patient_name, "payer": claim.payer_name,
             "service_date": claim.service_date, "billed_amount": claim.total_charge,
             "first_submitted_on": row["submitted_on"], "original_reference": first.payer_reference,
             "status": f"{status['category']} {status['category_text']}; {status['code_text']}",
             "payer_claim_number": parsed.get("payer_claim_number"), "paid_amount": status.get("paid_amount"),
             "check_number": status.get("check_number")}
    note, result = draft_note(llm, action, facts)
    if result is not None:
        db.add(AgentRun(claim_id=claim.id, agent=AGENT, model=result.model, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, latency_ms=result.latency_ms))
    followup.checks = [*(followup.checks or []), {
        "at": now.isoformat(), "by": user.email, "x12_276": x276, "x12_277": x277, "status": status,
        "payer_claim_number": parsed.get("payer_claim_number"), "action": action,
        "reasons": [r for r in reasons if r], "note": note}]
    followup.recommended_action = action
    followup.status = "open"
    followup.last_checked_at = now
    followup.next_check_on = (now.date() + timedelta(days=recheck_days)).isoformat() if recheck_days else None
    db.add(followup)
    audit.record(db, user.email, "ar.status_checked", "claim", claim.number,
                 {"status": f"{status['category']}:{status['code']}", "recommended": action}, commit=False)
    db.commit()
    log_step(db, claim, AGENT, f"Status check (276/277): {status['category']} {status['category_text']}, "
                               f"{status['code_text']}. Recommends: {ACTIONS[action].lower()}")
    events.publish("ar.updated", claim_id=claim.id)
    return followup


def act(db: Session, claim: Claim, user: User, action: str, note: str | None) -> FollowUp:
    followup = db.scalar(select(FollowUp).where(FollowUp.claim_id == claim.id))
    if followup is None or not followup.checks:
        raise ValueError("Check the claim's status first")
    latest = db.scalars(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.id.desc())).first()
    count = db.scalar(select(func.count(Submission.id)).where(Submission.claim_id == claim.id))
    if action == "resubmit":
        key = f"submit:{claim.id}:resubmit:{count}"
        enqueue(db, "submit", claim, key=key, payload={"frequency": "1", "key": key})
        message = "Resubmitted the claim (original receipt date cited for timely filing)"
    elif action in ("send_records", "request_remittance"):
        enqueue(db, "remit", claim, key=f"remit:{latest.id}:released:{len(followup.checks)}",
                payload={"submission_id": latest.id, "released": True}, delay_seconds=settings.payer_delay_seconds)
        message = "Sent the medical records" if action == "send_records" else "Requested a copy of the 835 remittance"
    elif action == "wait":
        message = f"Will check again on {followup.next_check_on}"
    else:
        raise ValueError(f"Unknown action {action}")
    followup.action_taken = action
    followup.status = "waiting" if action != "wait" else "open"
    audit.record(db, user.email, f"ar.{action}", "claim", claim.number, {"note": note}, commit=False)
    db.commit()
    log_step(db, claim, user.email, f"{user.name}: {message}" + (f" ({note})" if note else ""))
    events.publish("ar.updated", claim_id=claim.id)
    return followup


def resolve_paid(db: Session, claim: Claim) -> None:
    """Called when an 835 arrives for a claim that had a follow-up."""
    followup = db.scalar(select(FollowUp).where(FollowUp.claim_id == claim.id))
    if followup and followup.status != "resolved":
        followup.status = "resolved"
        db.commit()
