"""After submission: remittances, denials and appeals. One handler per job kind, like pipeline.py.

    remit            the payer answers a submission with an 835; we parse it like any 835 file,
                     record payments and open a Denial for each adjustment someone must act on
    triage           Denial Triage Agent: category, recommended action, deadline, odds
    draft_appeal     Appeal Agent: a letter whose every argument cites the record
    appeal_decision  the payer reviews a sent appeal and either pays or upholds

The payer side (adjudicate, review) and the provider side (parse, triage, draft) only
talk through 835 text and the letter's verified citations, as they would in reality.
"""

import hashlib
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.appeals import draft_appeal, letter_header
from app.agents.denials import ACTIONS, denial_groups, triage_denial
from app.config import settings
from app.core import audit, events
from app.core.llm import LLM, LLMResult
from app.core.workflow import (DENIAL_OPEN, TRANSITIONS, DenialStatus, Status, enqueue, log_step, transition)
from app.documents.pdf import parse_pdf
from app.models import AgentRun, Appeal, Claim, Denial, Remittance, Submission
from app.verticals.healthcare import payer
from app.verticals.healthcare.carc import CARCS, CATEGORIES, describe
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.schema import DOCUMENT_TYPES
from app.verticals.healthcare.x12_835 import build_835, parse_835
from app.verticals.healthcare.x12_837 import parse_837

GATEWAY = "Payer Gateway"
TRIAGE_AGENT = "Denial Triage Agent"
APPEAL_AGENT = "Appeal Agent"
SYSTEM = "Workflow Engine"
RECEIVER_ID = "HEALTHOPS"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _record_run(db: Session, claim: Claim, agent: str, result: LLMResult | None) -> None:
    if result:
        db.add(AgentRun(claim_id=claim.id, agent=agent, model=result.model, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, latency_ms=result.latency_ms))


def _publish(denial: Denial) -> None:
    events.publish("denial.updated", denial_id=denial.id, claim_id=denial.claim_id, status=denial.status)


def record_of(claim: Claim) -> dict:
    return {k: v for k, v in (claim.extracted or {}).items() if not k.startswith("_")}


def documents_of(claim: Claim) -> dict:
    return {d.kind: (d.id, parse_pdf(d.path)) for d in claim.documents if d.kind in DOCUMENT_TYPES}


# --- what the payer receives ------------------------------------------------------------------

def _split_name(name: str | None) -> tuple[str, str]:
    name = (name or "").strip()
    if "," in name:
        last, _, first = name.partition(",")
        return last.strip().upper(), first.strip().upper()
    parts = name.split()
    return (parts[-1].upper(), " ".join(parts[:-1]).upper()) if parts else ("UNKNOWN", "")


def payer_claim(claim: Claim, frequency: str = "1", modifiers: dict[str, list[str]] | None = None) -> dict:
    """The claim as the payer sees it (what an 837 would carry), built from the extracted record."""
    form = record_of(claim).get("claim_form") or {}
    value = lambda name: (form.get(name) or {}).get("value")  # noqa: E731
    last, first = _split_name(value("patient_name"))
    modifiers = modifiers or {}
    return {
        "claim_number": claim.number, "frequency": frequency, "payer_name": claim.payer_name,
        "member_id": value("member_id") or "", "patient_last": last, "patient_first": first,
        "service_date": claim.service_date, "prior_auth_number": value("prior_auth_number"),
        "provider_name": value("provider_name") or "Unknown provider", "provider_npi": value("provider_npi") or "",
        "patient_dob": value("patient_dob"),
        "diagnosis_codes": [d["code"] for d in (form.get("diagnosis_codes") or {}).get("value", [])],
        "lines": [{"cpt": l["cpt"], "units": l.get("units") or 1, "charge": float(l.get("charge") or 0),
                   "modifiers": modifiers.get(l["cpt"]) or list(l.get("modifiers") or [])}
                  for l in (form.get("line_items") or {}).get("value", [])],
    }


# --- 835 ingestion (shared by the payer simulation and file import) ---------------------------

def ingest_835(db: Session, raw: str, kind: str) -> tuple[list[Remittance], list[str]]:
    """Parse an 835 and attach each claim in it to our claim with the same number.
    Returns the remittances created and the claim numbers we don't know."""
    parsed = parse_835(raw)
    created, unknown = [], []
    for clp in parsed["claims"]:
        claim = db.scalar(select(Claim).where(Claim.number == clp["claim_number"]))
        if not claim:
            unknown.append(clp["claim_number"])
            continue
        remit = Remittance(claim_id=claim.id, kind=kind, payer_name=parsed["payer"].get("name") or "",
                           trace_number=parsed["payment"].get("trace") or "", raw=raw,
                           parsed={"payer": parsed["payer"], "payee": parsed["payee"], "payment": parsed["payment"],
                                   "control_number": parsed["control_number"], "warnings": parsed["warnings"],
                                   "claim": clp},
                           paid=clp["paid"], patient_responsibility=clp["patient_responsibility"])
        db.add(remit)
        db.flush()
        created.append(remit)
        process_remittance(db, claim, remit)
    db.commit()
    return created, unknown


def process_remittance(db: Session, claim: Claim, remit: Remittance) -> None:
    clp = remit.parsed["claim"]
    from app.ar import resolve_paid
    resolve_paid(db, claim)  # an 835 closes any A/R follow-up on the claim
    audit.record(db, GATEWAY, "remittance.received", "claim", claim.number,
                 {"trace": remit.trace_number, "status": clp["status_code"], "paid": clp["paid"],
                  "patient_responsibility": clp["patient_responsibility"],
                  "raw_sha256": hashlib.sha256(remit.raw.encode()).hexdigest()}, commit=False)
    if clp["status_code"] == "22":
        log_step(db, claim, GATEWAY, f"835 reversal: payer took back ${-clp['paid']:,.2f} paid earlier "
                                     f"(replaced by the corrected claim)")
        return

    groups = denial_groups(clp)
    summary = f"835 received ({clp['status']}): paid ${clp['paid']:,.2f}"
    if clp["patient_responsibility"]:
        summary += f", patient owes ${clp['patient_responsibility']:,.2f}"
    summary += f", {len(groups)} item(s) to work" if groups else ", nothing to work"
    log_step(db, claim, GATEWAY, summary, remittance_id=remit.id)

    # A corrected claim's remittance settles the denials it was sent to fix.
    if remit.kind == "corrected":
        for old in db.scalars(select(Denial).where(Denial.claim_id == claim.id,
                                                  Denial.status == DenialStatus.RESUBMITTED)):
            cpts = {l["cpt"] for l in old.lines}
            paid = sum(l["paid"] for l in clp["lines"] if l["cpt"] in cpts)
            old.status = DenialStatus.PAID_ON_RESUBMISSION if paid > 0 else DenialStatus.SUPERSEDED
            old.recovered = round(paid, 2)
            old.closed_at = _now()
            _publish(old)

    for g in groups:
        if remit.kind == "appeal":
            continue
        denial = Denial(claim_id=claim.id, remittance_id=remit.id, group_code=g["group"], reason_code=g["code"],
                        remark_codes=g["remarks"], lines=g["lines"], amount=g["amount"],
                        category="patient_responsibility" if g["group"] == "PR" else
                        (CARCS[g["code"]].category if g["code"] in CARCS else "other"),
                        status=DenialStatus.TRIAGE)
        db.add(denial)
        db.flush()
        log_step(db, claim, GATEWAY, f"{describe(g['group'], g['code'])} on {', '.join(l['cpt'] for l in g['lines'])} "
                                     f"(${g['amount']:,.2f})", denial_id=denial.id)
        db.commit()
        enqueue(db, "triage", claim, key=f"triage:{denial.id}", payload={"denial_id": denial.id})
    db.commit()
    settle_claim(db, claim)


def target_status(db: Session, claim: Claim) -> tuple[Status, float]:
    """The status a claim's denials and payments imply, and the amount paid so far."""
    denials = db.scalars(select(Denial).where(Denial.claim_id == claim.id)).all()
    paid = db.scalar(select(func.coalesce(func.sum(Remittance.paid), 0)).where(Remittance.claim_id == claim.id))
    if any(d.status == DenialStatus.RESUBMITTED for d in denials):
        return Status.SUBMITTED, paid
    if any(d.status == DenialStatus.APPEAL_SENT for d in denials):
        return Status.APPEALED, paid
    if any(d.status in DENIAL_OPEN and d.category != "patient_responsibility" for d in denials):
        return Status.DENIED, paid
    if paid > 0 or any(d.status == DenialStatus.PATIENT_BILLED for d in denials):
        return Status.PAID, paid
    return Status.CLOSED, paid


SETTLE_NOTES = {Status.DENIED: "Payer denied part or all of the claim", Status.APPEALED: "Appeal sent to the payer",
                Status.CLOSED: "Closed with nothing more to collect", Status.SUBMITTED: "Corrected claim sent"}


def settle_claim(db: Session, claim: Claim) -> None:
    """Move the claim to the status its denials and payments now imply."""
    target, paid = target_status(db, claim)
    current = Status(claim.status)
    if target != current and target in TRANSITIONS[current]:
        transition(db, claim, target, SYSTEM, f"Settled: payer paid ${paid:,.2f}" if target == Status.PAID
                   else SETTLE_NOTES.get(target))


# --- job handlers -------------------------------------------------------------------------------

def handle_remit(db: Session, claim: Claim, _llm: LLM | None, payload: dict) -> None:
    """The mock payer adjudicates a submission and sends back an 835."""
    submission = db.get(Submission, payload["submission_id"])
    profile = payer.profile_for(claim.payer_name)
    trace = f"{profile.id}{submission.id:07d}"
    if db.scalar(select(Remittance).where(Remittance.claim_id == claim.id, Remittance.trace_number == trace)):
        return  # already received: a retried job mustn't pay twice

    # The payer reads the 837 it was sent, not our database (older demo rows have no file yet).
    request = parse_837(submission.x12) if submission.x12 else submission.payload
    first = db.scalar(select(func.min(Submission.id)).where(Submission.claim_id == claim.id)) == submission.id
    if payer.withholds_remittance(request.get("member_id"), first, bool(payload.get("released"))):
        return  # the payer is silent: this claim will show up in accounts receivable follow-up
    clp = payer.adjudicate(request, _aware(submission.at).date())
    claims = [clp]
    if submission.frequency == "7":
        previous = db.scalars(select(Remittance).where(Remittance.claim_id == claim.id,
                                                       Remittance.kind.in_(("original", "corrected")))
                              .order_by(Remittance.id.desc())).first()
        if previous and previous.parsed["claim"]["status_code"] != "22":
            claims.insert(0, payer.reversal_of(previous.parsed["claim"]))
    remit = payer.remittance(claim.payer_name, {"name": request["provider_name"], "npi": request["provider_npi"]},
                             claims, _now().date(), trace)
    raw = build_835(remit, profile.id, RECEIVER_ID, submission.id * 10 + 1, _now())
    ingest_835(db, raw, "corrected" if submission.frequency == "7" else "original")


def handle_triage(db: Session, claim: Claim, llm: LLM | None, payload: dict) -> None:
    denial = db.get(Denial, payload["denial_id"])
    if denial.status != DenialStatus.TRIAGE:
        return
    first = db.scalar(select(func.min(Submission.at)).where(Submission.claim_id == claim.id))
    remit_date = _aware(denial.remittance.received_at).date().isoformat()
    record = record_of(claim)
    lines = (record.get("claim_form") or {}).get("line_items", {}).get("value", [])
    context = {"payer_name": claim.payer_name, "service_date": claim.service_date, "remit_date": remit_date,
               "first_submitted_on": _aware(first).date().isoformat() if first else remit_date,
               "claim_cpts": [l["cpt"] for l in lines]}
    code = f"{denial.group_code}-{denial.reason_code}"
    log_step(db, claim, TRIAGE_AGENT, f"Analysing {code} ({CATEGORIES.get(denial.category, denial.category).lower()})",
             denial_id=denial.id)

    result = triage_denial({"group": denial.group_code, "code": denial.reason_code, "remarks": denial.remark_codes,
                            "lines": denial.lines, "amount": denial.amount},
                           record, documents_of(claim), context, llm, model=settings.review_model)
    _record_run(db, claim, TRIAGE_AGENT, result.llm)
    if result.retrieval:
        top = result.retrieval[0]
        log_step(db, claim, TRIAGE_AGENT, f"Policy search: {top['id']} {top['title']} (BM25 {top['score']})"
                 + (f", using {result.policy_id}" if result.policy_id and result.policy_id != top["id"] else ""),
                 denial_id=denial.id)
    if result.criteria and result.llm:
        met = sum(1 for c in result.criteria if c["met"])
        log_step(db, claim, TRIAGE_AGENT, f"Checked the clinical note against {result.policy_id}: "
                                          f"{met} of {len(result.criteria)} criteria met", denial_id=denial.id)

    denial.category = result.category
    denial.recommended_action = result.action
    denial.deadline = result.deadline
    denial.win_probability = result.win_probability
    denial.expected_recovery = result.expected_recovery
    denial.policy_id = result.policy_id
    denial.triage = result.as_json()
    denial.status = DenialStatus.OPEN
    audit.record(db, TRIAGE_AGENT, "denial.triaged", "denial", str(denial.id),
                 {"claim": claim.number, "code": code, "action": result.action, "deadline": result.deadline},
                 commit=False)
    db.commit()
    log_step(db, claim, TRIAGE_AGENT, f"Recommendation: {ACTIONS[result.action].lower()}"
             + (f" by {result.deadline}" if result.deadline else ""), denial_id=denial.id)
    _publish(denial)

    if result.action == "appeal":
        start_appeal_draft(db, claim, denial)


def start_appeal_draft(db: Session, claim: Claim, denial: Denial) -> Appeal:
    appeal = db.scalar(select(Appeal).where(Appeal.denial_id == denial.id))
    if appeal is None:
        appeal = Appeal(denial_id=denial.id, status="drafting")
        db.add(appeal)
    else:
        appeal.status, appeal.draft = "drafting", {}
    db.commit()
    count = db.scalar(select(func.count(AgentRun.id)))
    enqueue(db, "draft_appeal", claim, key=f"draft_appeal:{appeal.id}:{count}", payload={"appeal_id": appeal.id})
    _publish(denial)
    return appeal


def appeal_facts(claim: Claim, denial: Denial) -> dict:
    record = record_of(claim)
    form = record.get("claim_form") or {}
    value = lambda name: (form.get(name) or {}).get("value")  # noqa: E731
    descriptions = {l["cpt"]: l.get("description") for l in (form.get("line_items") or {}).get("value", [])}
    carc = CARCS.get(denial.reason_code)
    return {
        "claim_number": claim.number,
        "payer_claim_number": denial.remittance.parsed["claim"]["payer_control_number"],
        "payer": claim.payer_name, "patient": claim.patient_name, "member_id": value("member_id"),
        "provider": value("provider_name"), "provider_npi": value("provider_npi"),
        "service_date": claim.service_date, "remit_date": _aware(denial.remittance.received_at).date().isoformat(),
        "denial_code": f"{denial.group_code}-{denial.reason_code}",
        "denial_reason": carc.description if carc else "Unlisted reason code",
        "remark_codes": denial.remark_codes, "denied_amount": denial.amount, "billed_amount": claim.total_charge,
        "services": [{"cpt": l["cpt"], "description": descriptions.get(l["cpt"]), "charge": l["charge"]}
                     for l in denial.lines],
        "diagnosis_codes": [d["code"] for d in (form.get("diagnosis_codes") or {}).get("value", [])],
        "prior_auth_number": value("prior_auth_number"),
    }


def handle_draft_appeal(db: Session, claim: Claim, llm: LLM | None, payload: dict) -> None:
    appeal = db.get(Appeal, payload["appeal_id"])
    if appeal.status != "drafting":
        return
    denial = appeal.denial
    if llm is None:
        appeal.status, appeal.draft = "failed", {"error": f"No LLM configured: set {settings.llm_key_name} to draft appeals"}
        db.commit()
        _publish(denial)
        return
    log_step(db, claim, APPEAL_AGENT, f"Drafting the appeal for {denial.group_code}-{denial.reason_code}",
             denial_id=denial.id)
    facts = appeal_facts(claim, denial)
    draft = draft_appeal(llm, facts, denial.triage or {}, documents_of(claim), get_policy(denial.policy_id),
                         model=settings.review_model)
    _record_run(db, claim, APPEAL_AGENT, draft.llm)
    stats = draft.stats
    header = letter_header(facts, facts["provider"] or "Provider", _now().date())
    appeal.draft = {**draft.as_json(), "header": header, "facts": facts}
    appeal.status = "draft"
    db.commit()
    log_step(db, claim, APPEAL_AGENT,
             f"Draft ready: {stats['paragraphs']} paragraph(s), {stats['verified_citations']} of {stats['citations']} "
             f"citations verified" + (f", {stats['removed_paragraphs']} unsupported paragraph(s) removed"
                                      if stats["removed_paragraphs"] else ""), denial_id=denial.id)
    _publish(denial)


def citations_of(paragraphs: list[dict]) -> list[dict]:
    return [c for p in paragraphs for c in p.get("citations", [])]


def handle_appeal_decision(db: Session, claim: Claim, _llm: LLM | None, payload: dict) -> None:
    """The payer's reviewer reads the appeal. Only citations verified against the record count."""
    appeal = db.get(Appeal, payload["appeal_id"])
    if appeal.status != "sent":
        return
    denial = appeal.denial
    decision = payer.review_appeal(denial.category, [l["cpt"] for l in denial.lines],
                                   citations_of(appeal.letter.get("paragraphs", [])), record_of(claim),
                                   claim.service_date, get_policy(denial.policy_id))
    appeal.decided_at = _now()
    appeal.outcome_reason = decision.reason
    appeal.draft = {**appeal.draft, "decision": {"satisfied": decision.satisfied, "missing": decision.missing}}
    if decision.overturned:
        appeal.status = "overturned"
        original = denial.remittance.parsed["claim"]
        clp = payer.appeal_payment(original, [l["cpt"] for l in denial.lines], claim.payer_name)
        profile = payer.profile_for(claim.payer_name)
        remit = payer.remittance(claim.payer_name, denial.remittance.parsed["payee"], [clp], _now().date(),
                                 f"{profile.id}A{appeal.id:06d}")
        raw = build_835(remit, profile.id, RECEIVER_ID, appeal.id * 10 + 7, _now())
        db.commit()
        created, _ = ingest_835(db, raw, "appeal")
        denial.status = DenialStatus.OVERTURNED
        denial.recovered = round(sum(r.paid for r in created), 2)
        denial.closed_at = _now()
        log_step(db, claim, GATEWAY, f"Appeal overturned: {decision.reason} Paid ${denial.recovered:,.2f}",
                 denial_id=denial.id)
    else:
        appeal.status = "upheld"
        denial.status = DenialStatus.UPHELD
        log_step(db, claim, GATEWAY, f"Appeal decided. {decision.reason}", denial_id=denial.id)
    audit.record(db, GATEWAY, f"appeal.{appeal.status}", "denial", str(denial.id),
                 {"claim": claim.number, "reason": decision.reason, "recovered": denial.recovered}, commit=False)
    db.commit()
    _publish(denial)
    settle_claim(db, claim)


def give_up(db: Session, kind: str, payload: dict, message: str) -> None:
    """A denial job failed for good: leave the work with a person instead of stuck."""
    if kind == "triage":
        denial = db.get(Denial, payload["denial_id"])
        if denial and denial.status == DenialStatus.TRIAGE:
            denial.status, denial.recommended_action = DenialStatus.OPEN, "manual_review"
            denial.triage = {**(denial.triage or {}), "reasons": [f"Automatic triage failed: {message[:200]}"]}
            db.commit()
            _publish(denial)
    elif kind == "draft_appeal":
        appeal = db.get(Appeal, payload["appeal_id"])
        if appeal and appeal.status == "drafting":
            appeal.status, appeal.draft = "failed", {"error": message[:300]}
            db.commit()
            _publish(appeal.denial)


HANDLERS = {"remit": handle_remit, "triage": handle_triage, "draft_appeal": handle_draft_appeal,
            "appeal_decision": handle_appeal_decision}
