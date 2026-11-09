"""Denials worklist, denial detail, the actions a biller takes, appeals and 835 import."""

from datetime import date, datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.agents.appeals import letter_text
from app.agents.denials import ACTIONS
from app.api.serializers import claim_summary, document_json, event_json, iso
from app.config import settings
from app.core import audit, events
from app.core.auth import current_user, require_role
from app.core.workflow import DENIAL_OPEN, DENIAL_WAITING, DenialStatus, Status, enqueue, transition
from app.db import get_db
from app.models import AgentRun, Appeal, Claim, ClaimEvent, Denial, Remittance, User
from app.revenue import settle_claim, start_appeal_draft, ingest_835
from app.verticals.healthcare.carc import CARCS, CATEGORIES, GROUPS, RARCS
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.x12_835 import X12Error

router = APIRouter(prefix="/api", tags=["denials"])

STATUS_LABELS = {
    DenialStatus.TRIAGE: "Analysing", DenialStatus.OPEN: "Needs action", DenialStatus.APPEAL_SENT: "Appeal sent",
    DenialStatus.RESUBMITTED: "Corrected claim sent", DenialStatus.UPHELD: "Appeal upheld",
    DenialStatus.OVERTURNED: "Overturned", DenialStatus.PAID_ON_RESUBMISSION: "Paid on resubmission",
    DenialStatus.PATIENT_BILLED: "Patient billed", DenialStatus.WRITTEN_OFF: "Written off",
    DenialStatus.SUPERSEDED: "Superseded",
}


def _days_left(deadline: str | None) -> int | None:
    return (date.fromisoformat(deadline) - datetime.now(timezone.utc).date()).days if deadline else None


def denial_summary(d: Denial, appeal: Appeal | None = None) -> dict:
    carc = CARCS.get(d.reason_code)
    return {
        "id": d.id, "claim_id": d.claim_id, "claim_number": d.claim.number, "patient_name": d.claim.patient_name,
        "payer_name": d.claim.payer_name, "service_date": d.claim.service_date,
        "code": f"{d.group_code}-{d.reason_code}", "group": d.group_code, "group_label": GROUPS.get(d.group_code),
        "reason": carc.description if carc else "Unlisted reason code",
        "remarks": [{"code": r, "text": RARCS.get(r, "")} for r in d.remark_codes],
        "category": d.category, "category_label": CATEGORIES.get(d.category, d.category),
        "amount": d.amount, "lines": d.lines, "status": d.status, "status_label": STATUS_LABELS.get(d.status, d.status),
        "recommended_action": d.recommended_action,
        "recommended_label": ACTIONS.get(d.recommended_action or "", None),
        "action_taken": d.action_taken, "deadline": d.deadline, "days_left": _days_left(d.deadline),
        "win_probability": d.win_probability, "expected_recovery": d.expected_recovery, "recovered": d.recovered,
        "policy_id": d.policy_id, "appeal_status": appeal.status if appeal else None,
        "created_at": iso(d.created_at), "closed_at": iso(d.closed_at),
    }


def _load(db: Session, denial_id: int) -> Denial:
    denial = db.scalar(select(Denial).where(Denial.id == denial_id)
                       .options(selectinload(Denial.claim).selectinload(Claim.documents),
                                selectinload(Denial.remittance)))
    if not denial:
        raise HTTPException(404, "Denial not found")
    return denial


def remittance_json(r: Remittance) -> dict:
    return {"id": r.id, "kind": r.kind, "payer_name": r.payer_name, "trace_number": r.trace_number,
            "paid": r.paid, "patient_responsibility": r.patient_responsibility, "received_at": iso(r.received_at),
            "claim": r.parsed.get("claim"), "payment": r.parsed.get("payment"), "warnings": r.parsed.get("warnings", [])}


def appeal_json(a: Appeal | None) -> dict | None:
    if a is None:
        return None
    return {"id": a.id, "status": a.status, "draft": a.draft, "letter": a.letter, "edited_by": a.edited_by,
            "sent_by": a.sent_by, "sent_at": iso(a.sent_at), "decided_at": iso(a.decided_at),
            "outcome_reason": a.outcome_reason}


@router.get("/denials")
def list_denials(view: Literal["open", "waiting", "closed", "all"] = "open", db: Session = Depends(get_db),
                 _: User = Depends(current_user)):
    query = select(Denial).options(selectinload(Denial.claim)).order_by(Denial.id.desc())
    states = {"open": DENIAL_OPEN, "waiting": DENIAL_WAITING,
              "closed": set(DenialStatus) - DENIAL_OPEN - DENIAL_WAITING}.get(view)
    if states:
        query = query.where(Denial.status.in_([str(s) for s in states]))
    denials = db.scalars(query).all()
    appeals = {a.denial_id: a for a in db.scalars(select(Appeal).where(Appeal.denial_id.in_([d.id for d in denials])))}
    rows = [denial_summary(d, appeals.get(d.id)) for d in denials]
    if view == "open":  # most money recoverable soonest first
        rows.sort(key=lambda r: (r["status"] == DenialStatus.TRIAGE, -(r["expected_recovery"] or 0),
                                 r["days_left"] if r["days_left"] is not None else 9999))
    return rows


@router.get("/denials/summary")
def denials_summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    denials = db.scalars(select(Denial)).all()
    open_ = [d for d in denials if d.status in DENIAL_OPEN]
    soon = [d for d in open_ if d.deadline and (_days_left(d.deadline) or 0) <= 14]
    return {"open": len(open_), "open_amount": round(sum(d.amount for d in open_), 2),
            "expected_recovery": round(sum(d.expected_recovery or 0 for d in open_), 2),
            "due_soon": len(soon), "waiting": sum(1 for d in denials if d.status in DENIAL_WAITING),
            "recovered": round(sum(d.recovered for d in denials), 2)}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _relevant(event: ClaimEvent, denial: Denial) -> bool:
    """This denial's own steps, plus claim-level changes since it arrived."""
    linked = (event.data or {}).get("denial_id")
    if linked is not None:
        return linked == denial.id
    return event.type == "transition" and _aware(event.at) >= _aware(denial.created_at)


@router.get("/denials/{denial_id}")
def get_denial(denial_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    d = _load(db, denial_id)
    claim = d.claim
    appeal = db.scalar(select(Appeal).where(Appeal.denial_id == d.id))
    remits = db.scalars(select(Remittance).where(Remittance.claim_id == claim.id).order_by(Remittance.id)).all()
    events_ = db.scalars(select(ClaimEvent).where(ClaimEvent.claim_id == claim.id).order_by(ClaimEvent.id)).all()
    runs = db.scalars(select(AgentRun).where(AgentRun.claim_id == claim.id,
                                             AgentRun.agent.in_(("Denial Triage Agent", "Appeal Agent")))).all()
    others = db.scalars(select(Denial).where(Denial.claim_id == claim.id, Denial.id != d.id)
                        .options(selectinload(Denial.claim))).all()
    policy = get_policy(d.policy_id)
    return {
        **denial_summary(d, appeal),
        "triage": d.triage, "claim": claim_summary(claim, 0), "documents": [document_json(doc) for doc in claim.documents],
        "remittance": remittance_json(d.remittance), "remittances": [remittance_json(r) for r in remits],
        "appeal": appeal_json(appeal), "policy": policy,
        "letter_text": letter_text(appeal.draft["header"], (appeal.letter or appeal.draft).get("paragraphs", []),
                                   (appeal.letter or appeal.draft).get("attachments", []))
        if appeal and appeal.draft.get("header") else None,
        "events": [event_json(e) for e in events_ if _relevant(e, d)][-40:],
        "agent_runs": [{"agent": r.agent, "model": r.model, "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens, "latency_ms": r.latency_ms} for r in runs],
        "other_denials": [denial_summary(o) for o in others],
        "actions": ACTIONS,
    }


@router.get("/remittances/{remittance_id}/raw", response_class=PlainTextResponse)
def remittance_raw(remittance_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    remit = db.get(Remittance, remittance_id)
    if not remit:
        raise HTTPException(404, "Remittance not found")
    return remit.raw


@router.get("/policies/{policy_id}")
def policy(policy_id: str, _: User = Depends(current_user)):
    p = get_policy(policy_id)
    if not p:
        raise HTTPException(404, "Policy not found")
    return p


# --- actions ------------------------------------------------------------------------------------

class Action(BaseModel):
    action: Literal["write_off", "bill_patient", "corrected_claim", "draft_appeal"]
    note: str | None = Field(default=None, max_length=2000)


def _log(db: Session, denial: Denial, user: User, message: str, **data) -> None:
    db.add(ClaimEvent(claim_id=denial.claim_id, type="decision", actor=user.email, message=message,
                      data={"denial_id": denial.id, **data}))


@router.post("/denials/{denial_id}/action")
def act(denial_id: int, body: Action, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    denial = _load(db, denial_id)
    claim = denial.claim
    if denial.status not in DENIAL_OPEN or denial.status == DenialStatus.TRIAGE:
        raise HTTPException(409, f"This denial is {STATUS_LABELS.get(denial.status, denial.status).lower()}")
    code = f"{denial.group_code}-{denial.reason_code}"

    if body.action == "write_off":
        if not (body.note or "").strip():
            raise HTTPException(400, "Say why it's written off")
        denial.status, denial.action_taken, denial.closed_at = DenialStatus.WRITTEN_OFF, "write_off", datetime.now(timezone.utc)
        _log(db, denial, user, f"{user.name} wrote off {code} (${denial.amount:,.2f}): {body.note}")
    elif body.action == "bill_patient":
        if denial.group_code != "PR" and denial.category != "eligibility":
            raise HTTPException(400, f"{code} is the provider's write-off under contract; it can't be billed to the patient")
        denial.status, denial.action_taken, denial.closed_at = DenialStatus.PATIENT_BILLED, "bill_patient", datetime.now(timezone.utc)
        from app.billing import bill_patient
        statement = bill_patient(db, denial, user.email)
        _log(db, denial, user, f"{user.name} billed the patient ${denial.amount:,.2f} on statement {statement.number}")
    elif body.action == "corrected_claim":
        fix = (denial.triage or {}).get("fix")
        if denial.category != "coding_bundling" or not fix:
            raise HTTPException(400, "Only bundling denials with a documented fix are corrected this way")
        if Status(claim.status) != Status.DENIED:
            raise HTTPException(409, f"Claim is {claim.status}, not denied")
        denial.status, denial.action_taken = DenialStatus.RESUBMITTED, "corrected_claim"
        _log(db, denial, user, f"{user.name} sent a corrected claim: modifier {fix['add_modifier']} on CPT {fix['cpt']}")
        db.commit()
        transition(db, claim, Status.SUBMITTED, user.email, f"Corrected claim: modifier {fix['add_modifier']} on {fix['cpt']}")
        count = db.scalar(select(func.count(Denial.id)).where(Denial.claim_id == claim.id))
        enqueue(db, "submit", claim, key=f"submit:{claim.id}:7:{denial.id}",
                payload={"frequency": "7", "key": f"submit:{claim.id}:7:{denial.id}:{count}",
                         "modifiers": {fix["cpt"]: [fix["add_modifier"]]}})
    elif body.action == "draft_appeal":
        existing = db.scalar(select(Appeal).where(Appeal.denial_id == denial.id))
        if existing and existing.status in ("drafting", "sent"):
            raise HTTPException(409, f"The appeal is already {existing.status}")
        _log(db, denial, user, f"{user.name} asked the Appeal Agent for a draft")
        db.commit()
        start_appeal_draft(db, claim, denial)
    audit.record(db, user.email, f"denial.{body.action}", "denial", str(denial.id),
                 {"claim": claim.number, "code": code, "amount": denial.amount, "note": body.note,
                  "recommended": denial.recommended_action}, commit=False)
    db.commit()
    events.publish("denial.updated", denial_id=denial.id, claim_id=claim.id, status=denial.status)
    if body.action in ("write_off", "bill_patient"):
        settle_claim(db, claim)
    return get_denial(denial_id, db, user)


class ParagraphEdit(BaseModel):
    source_index: int | None = None  # which draft paragraph this came from; None for a new one
    text: str = Field(min_length=1, max_length=4000)


class LetterEdit(BaseModel):
    paragraphs: list[ParagraphEdit] = Field(min_length=1, max_length=12)


def _final_paragraphs(draft: dict, edits: list[ParagraphEdit]) -> list[dict]:
    """Citations come from the server's verified draft, never from the client: an edited
    paragraph keeps its source's citations, a new paragraph has none."""
    source = draft.get("paragraphs", [])
    out = []
    for e in edits:
        original = source[e.source_index] if e.source_index is not None and 0 <= e.source_index < len(source) else None
        text = " ".join(e.text.split())
        out.append({"kind": original["kind"] if original else "argument", "text": text,
                    "citations": original["citations"] if original else [],
                    "source_index": e.source_index, "edited": original is None or original["text"] != text})
    return out


@router.put("/appeals/{appeal_id}")
def edit_appeal(appeal_id: int, body: LetterEdit, db: Session = Depends(get_db),
                user: User = Depends(require_role("biller"))):
    appeal = db.get(Appeal, appeal_id)
    if not appeal or appeal.status != "draft":
        raise HTTPException(409, "Only a draft appeal can be edited")
    paragraphs = _final_paragraphs(appeal.draft, body.paragraphs)
    appeal.letter = {"paragraphs": paragraphs, "attachments": appeal.draft.get("attachments", [])}
    appeal.edited_by = user.email
    db.commit()
    return get_denial(appeal.denial_id, db, user)


@router.post("/appeals/{appeal_id}/send")
def send_appeal(appeal_id: int, body: LetterEdit, db: Session = Depends(get_db),
                user: User = Depends(require_role("biller"))):
    appeal = db.get(Appeal, appeal_id)
    if not appeal or appeal.status != "draft":
        raise HTTPException(409, "Only a draft appeal can be sent")
    denial = _load(db, appeal.denial_id)
    if denial.status not in (DenialStatus.OPEN, DenialStatus.UPHELD):
        raise HTTPException(409, "This denial isn't waiting for an action")
    paragraphs = _final_paragraphs(appeal.draft, body.paragraphs)
    edited = [p for p in paragraphs if p["edited"]]
    appeal.letter = {"paragraphs": paragraphs, "attachments": appeal.draft.get("attachments", [])}
    appeal.status, appeal.sent_by, appeal.sent_at = "sent", user.email, datetime.now(timezone.utc)
    denial.status, denial.action_taken = DenialStatus.APPEAL_SENT, "appeal"
    text = letter_text(appeal.draft["header"], paragraphs, appeal.letter["attachments"])
    _log(db, denial, user, f"{user.name} sent the appeal ({len(paragraphs)} paragraphs, {len(edited)} edited)")
    import hashlib
    audit.record(db, user.email, "appeal.sent", "denial", str(denial.id),
                 {"claim": denial.claim.number, "letter_sha256": hashlib.sha256(text.encode()).hexdigest(),
                  "paragraphs_edited": len(edited),
                  "citations": sum(len(p["citations"]) for p in paragraphs)}, commit=False)
    db.commit()
    events.publish("denial.updated", denial_id=denial.id, claim_id=denial.claim_id, status=denial.status)
    settle_claim(db, denial.claim)
    enqueue(db, "appeal_decision", denial.claim, key=f"appeal_decision:{appeal.id}", payload={"appeal_id": appeal.id},
            delay_seconds=settings.payer_delay_seconds)
    return get_denial(denial.id, db, user)


@router.post("/remittances/import")
async def import_835(file: UploadFile = File(...), db: Session = Depends(get_db),
                     user: User = Depends(require_role("biller"))):
    """Load an 835 file from a clearinghouse. Claims are matched by their claim number (CLP01)."""
    data = await file.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(400, "835 files over 5 MB aren't supported here")
    try:
        created, unknown = ingest_835(db, data.decode("utf-8", errors="replace"), "imported")
    except X12Error as exc:
        raise HTTPException(400, f"Not a valid 835: {exc}")
    audit.record(db, user.email, "remittance.imported", "file", file.filename or "835",
                 {"matched": [r.claim_id for r in created], "unknown": unknown})
    return {"matched": len(created), "unknown_claims": unknown,
            "remittances": [remittance_json(r) for r in created]}
