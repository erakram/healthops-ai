"""Prior authorization workflow: one handler per job kind, like pipeline.py and revenue.py.

    received -> reading -> checking -> not_required | ineligible            (rules, no LLM)
                                    -> needs_documentation | ready           (criteria checked, request drafted)
    needs_documentation | ready | pended | denied -> checking                (an addendum note was added)
    needs_documentation | ready | pended -> submitted                       (a person sends it)
    submitted -> approved | pended | denied                                 (the payer's FHIR response)
    any open state -> withdrawn;  reading/checking -> failed -> reading      (retry)

Jobs: auth_read (Document Agent), auth_check (requirement + Clinical Criteria Agent +
Request Agent), auth_decide (the mock payer reviews the PAS request after a delay).
"""

import time
from datetime import datetime, timedelta, timezone
from enum import StrEnum

from sqlalchemy.orm import Session

from app.agents.authorization import (ACTIONS, Assessment, assess_documentation, check_requirement, draft_request,
                                      request_header, services_of)
from app.agents.cited_writing import source_label
from app.agents.extraction import extract_document
from app.config import settings
from app.core import audit, events
from app.core.llm import LLM, LLMError, LLMResult
from app.core.workflow import InvalidTransition, enqueue
from app.documents.pdf import parse_pdf
from app.models import AgentRun, Authorization, AuthEvent, Document
from app.verticals.healthcare import fhir_pas, payer
from app.verticals.healthcare.letters import determination_letter
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.schema import DOCUMENT_TYPES

DOCUMENT_AGENT = "Document Agent"
CRITERIA_AGENT = "Clinical Criteria Agent"
REQUEST_AGENT = "Request Agent"
GATEWAY = "Payer Gateway"
SYSTEM = "Workflow Engine"


class AuthStatus(StrEnum):
    RECEIVED = "received"
    READING = "reading"
    CHECKING = "checking"
    NOT_REQUIRED = "not_required"
    INELIGIBLE = "ineligible"
    NEEDS_DOCUMENTATION = "needs_documentation"
    READY = "ready"
    SUBMITTED = "submitted"
    APPROVED = "approved"
    PENDED = "pended"
    DENIED = "denied"
    WITHDRAWN = "withdrawn"
    FAILED = "failed"


S = AuthStatus
TRANSITIONS: dict[AuthStatus, set[AuthStatus]] = {
    S.RECEIVED: {S.READING, S.FAILED, S.WITHDRAWN},
    S.READING: {S.CHECKING, S.FAILED},
    S.CHECKING: {S.NOT_REQUIRED, S.INELIGIBLE, S.NEEDS_DOCUMENTATION, S.READY, S.FAILED},
    S.NEEDS_DOCUMENTATION: {S.CHECKING, S.SUBMITTED, S.WITHDRAWN},
    S.READY: {S.CHECKING, S.SUBMITTED, S.WITHDRAWN},
    S.SUBMITTED: {S.APPROVED, S.PENDED, S.DENIED},
    S.PENDED: {S.CHECKING, S.SUBMITTED, S.WITHDRAWN},
    S.DENIED: {S.CHECKING, S.WITHDRAWN},
    S.FAILED: {S.READING},
    S.NOT_REQUIRED: set(), S.INELIGIBLE: {S.CHECKING, S.WITHDRAWN}, S.APPROVED: set(), S.WITHDRAWN: set(),
}
SUBMITTABLE = {S.NEEDS_DOCUMENTATION, S.READY, S.PENDED}
CAN_ADD_DOCUMENTS = {S.NEEDS_DOCUMENTATION, S.READY, S.PENDED, S.DENIED, S.INELIGIBLE}
OPEN = {S.RECEIVED, S.READING, S.CHECKING, S.NEEDS_DOCUMENTATION, S.READY, S.PENDED, S.FAILED}
TURNAROUND = {"urgent": timedelta(hours=72), "routine": timedelta(days=7)}  # CMS-0057-F payer response limits


def now() -> datetime:
    return datetime.now(timezone.utc)


def aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def transition(db: Session, auth: Authorization, to: AuthStatus, actor: str, note: str | None = None) -> None:
    current = AuthStatus(auth.status)
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"{auth.number} can't go from {current} to {to}")
    auth.status = to
    db.add(AuthEvent(authorization_id=auth.id, type="transition", actor=actor,
                     message=note or f"Moved to {to.replace('_', ' ')}", data={"from": current, "to": to}))
    audit.record(db, actor, "authorization.transition", "authorization", auth.number,
                 {"from": current, "to": to, "note": note}, commit=False)
    db.commit()
    events.publish("auth.updated", authorization_id=auth.id, status=to)


def log_step(db: Session, auth: Authorization, actor: str, message: str, kind: str = "step", **data) -> None:
    db.add(AuthEvent(authorization_id=auth.id, type=kind, actor=actor, message=message, data=data))
    db.commit()
    events.publish("auth.step", authorization_id=auth.id, agent=actor, message=message)


def _record_run(db: Session, auth: Authorization, agent: str, result: LLMResult | None) -> None:
    if result:
        db.add(AgentRun(authorization_id=auth.id, agent=agent, model=result.model, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, latency_ms=result.latency_ms))


def record_of(auth: Authorization) -> dict:
    return {k: v for k, v in (auth.extracted or {}).items() if not k.startswith("_")}


def documents_of(auth: Authorization) -> dict:
    """Parsed documents keyed by kind; the second and later clinical notes are clinical_note_2, _3 (addenda)."""
    docs, notes = {}, 0
    for d in auth.documents:
        if d.kind == "clinical_note":
            notes += 1
            docs["clinical_note" if notes == 1 else f"clinical_note_{notes}"] = (d.id, parse_pdf(d.path))
        elif d.kind in DOCUMENT_TYPES and d.kind not in docs:
            docs[d.kind] = (d.id, parse_pdf(d.path))
    return docs


def queue_check(db: Session, auth: Authorization) -> None:
    enqueue(db, "auth_check", None, key=f"auth_check:{auth.id}:{time.time_ns()}", payload={"authorization_id": auth.id})


def facts_of(auth: Authorization) -> dict:
    record = record_of(auth)
    order = record.get("service_order") or {}
    value = lambda name: (order.get(name) or {}).get("value")  # noqa: E731
    return {"request_number": auth.number, "order_number": value("order_number"), "patient": auth.patient_name,
            "member_id": auth.member_id, "payer": auth.payer_name, "provider": value("ordering_provider") or "Provider",
            "provider_npi": value("provider_npi"), "urgency": auth.urgency, "scheduled_date": auth.scheduled_date,
            "services": auth.services,
            "diagnosis_codes": [d["code"] for d in (order.get("diagnosis_codes") or {}).get("value", [])]}


# --- handlers ---------------------------------------------------------------------------------

def handle_auth_read(db: Session, _claim, llm: LLM | None, payload: dict) -> None:
    auth = db.get(Authorization, payload["authorization_id"])
    if llm is None:
        raise LLMError(f"No LLM configured: set {settings.llm_key_name} to read authorization packets")
    if auth.status != AuthStatus.READING:
        transition(db, auth, AuthStatus.READING, SYSTEM, "Document Agent started")
    record: dict = {}
    for doc in auth.documents:
        if doc.kind is None:
            log_step(db, auth, DOCUMENT_AGENT, f"Reading {doc.filename}", document_id=doc.id)
            parsed = parse_pdf(doc.path)
            if parsed.ocr_pages or parsed.needs_ocr:
                log_step(db, auth, DOCUMENT_AGENT, f"{doc.filename}: scanned page(s) "
                         + (f"{parsed.ocr_pages} read with on-device OCR" if parsed.ocr_pages else
                            f"{parsed.needs_ocr} need a person (no OCR on this server)"), document_id=doc.id)
            result = extract_document(llm, parsed, doc.id, model=settings.extraction_model)
            _record_run(db, auth, DOCUMENT_AGENT, result.llm)
            doc.pages, doc.text, doc.kind, doc.fields = parsed.pages, parsed.text, result.document_type, result.fields
            db.commit()
            label = DOCUMENT_TYPES.get(result.document_type, {}).get("label", "Unrecognised document")
            grounded = sum(1 for f in result.fields.values() if f.get("evidence"))
            log_step(db, auth, DOCUMENT_AGENT, f"{doc.filename} is a {label.lower()}: {len(result.fields)} fields read, "
                                               f"{grounded} matched to the page", document_id=doc.id)
        if doc.kind in DOCUMENT_TYPES and doc.kind not in record:
            record[doc.kind] = doc.fields or {}
    _apply_record(auth, record)
    db.commit()
    transition(db, auth, AuthStatus.CHECKING, SYSTEM, "Documents read, checking the payer's requirements")
    queue_check(db, auth)


def _apply_record(auth: Authorization, record: dict) -> None:
    auth.extracted = record
    order, card = record.get("service_order") or {}, record.get("insurance_card") or {}
    value = lambda doc, name: (doc.get(name) or {}).get("value")  # noqa: E731
    auth.patient_name = value(order, "patient_name") or value(card, "member_name")
    auth.payer_name = value(order, "payer_name") or value(card, "payer_name")
    auth.member_id = value(order, "member_id") or value(card, "member_id")
    auth.scheduled_date = value(order, "scheduled_date")
    auth.services = services_of(record)
    urgency = str(value(order, "urgency") or "routine").strip().lower()
    auth.urgency = "urgent" if urgency in ("urgent", "stat", "expedited") else "routine"


def handle_auth_check(db: Session, _claim, llm: LLM | None, payload: dict) -> None:
    auth = db.get(Authorization, payload["authorization_id"])
    if auth.status != AuthStatus.CHECKING:
        return
    record = record_of(auth)
    requirement = check_requirement(record, now().date())
    auth.requirement = requirement
    db.commit()
    for reason in requirement["reasons"]:
        log_step(db, auth, SYSTEM, reason)
    if not requirement["eligible"]:
        transition(db, auth, AuthStatus.INELIGIBLE, SYSTEM, "Coverage isn't active on the scheduled date")
        return
    if not requirement["required"]:
        auth.assessment = {"action": "none", "reasons": requirement["reasons"], "criteria": [], "missing": []}
        transition(db, auth, AuthStatus.NOT_REQUIRED, SYSTEM, "No authorization needed: schedule the service")
        return

    policy = get_policy(requirement["policy_id"])
    docs = documents_of(auth)
    notes = {k: v for k, v in docs.items() if k.startswith("clinical_note")}
    if policy is None:
        assessment = Assessment([], [], "submit", ["No medical policy with criteria applies; submit the request "
                                                   "with the clinical notes attached."])
    elif not notes:
        criteria = [{"id": c["id"], "text": c["text"], "met": False, "model_said_met": False, "quote": "",
                     "explanation": "No clinical note in the packet.", "source": None, "evidence": None}
                    for c in policy["criteria"]]
        assessment = Assessment(criteria, [{"id": c["id"], "text": c["text"], "ask": "Attach the clinical note.",
                                            "unverified_quote": None} for c in criteria], "request_documentation",
                                ["There's no clinical note in the packet. Attach the visit note."])
    else:
        if llm is None:
            raise LLMError(f"No LLM configured: set {settings.llm_key_name} to check documentation")
        log_step(db, auth, CRITERIA_AGENT, f"Checking {len(notes)} note(s) against {policy['id']} {policy['title']}")
        assessment = assess_documentation(llm, policy, auth.services, notes, model=settings.review_model)
        _record_run(db, auth, CRITERIA_AGENT, assessment.llm)
        met = sum(1 for c in assessment.criteria if c["met"])
        log_step(db, auth, CRITERIA_AGENT, f"{met} of {len(assessment.criteria)} criteria documented"
                 + (f"; missing {', '.join(m['id'] for m in assessment.missing)}" if assessment.missing else ""))
    auth.assessment = {**assessment.as_json(), "policy_id": policy["id"] if policy else None}
    db.commit()

    if llm is not None and notes:
        log_step(db, auth, REQUEST_AGENT, "Drafting the medical necessity summary")
        facts = facts_of(auth)
        draft = draft_request(llm, facts, auth.assessment, docs, policy, model=settings.review_model)
        _record_run(db, auth, REQUEST_AGENT, draft.llm)
        auth.draft = {**draft.as_json(), "header": request_header(facts, now().date()), "facts": facts}
        auth.letter = {}
        s = draft.stats
        log_step(db, auth, REQUEST_AGENT, f"Draft ready: {s['verified_citations']} of {s['citations']} citations verified, "
                                          f"{s['evidence_cited']}/{s['evidence_required']} required evidence cited"
                 + (f", {s['removed_paragraphs']} unsupported paragraph(s) removed" if s["removed_paragraphs"] else ""))
        db.commit()
    target = AuthStatus.READY if assessment.action == "submit" else AuthStatus.NEEDS_DOCUMENTATION
    transition(db, auth, target, SYSTEM, ACTIONS[assessment.action])


def submit(db: Session, auth: Authorization, actor: str, paragraphs: list[dict]) -> None:
    """Send the request to the payer as a PAS Bundle. `paragraphs` are the final letter
    paragraphs (citations come from the server's verified draft, never from the client)."""
    facts = facts_of(auth)
    profile = payer.profile_for(auth.payer_name)
    docs = documents_of(auth)
    citations = [{"source": c["source"], "quote": c["quote"]} for p in paragraphs for c in p.get("citations", [])
                 if c.get("verified")]
    header = auth.draft.get("header") or request_header(facts, now().date())
    from app.agents.appeals import letter_text
    letter = letter_text(header, paragraphs, auth.draft.get("attachments", []))
    attached = [{"title": source_label(kind), "text": pdf.text} for kind, (_, pdf) in docs.items()
                if kind.startswith("clinical_note") or kind == "service_order"]
    auth.attempts += 1
    order = {"request_number": auth.number, "attempt": auth.attempts, "order_number": facts["order_number"] or auth.number,
             "urgency": auth.urgency, "scheduled_date": auth.scheduled_date or now().date().isoformat(),
             "patient": {"name": auth.patient_name or "", "dob": ((record_of(auth).get("service_order") or {})
                                                                  .get("patient_dob") or {}).get("value"),
                         "member_id": auth.member_id or ""},
             "payer": {"name": profile.name, "id": profile.id},
             "provider": {"name": facts["provider"], "npi": facts["provider_npi"] or ""},
             "services": auth.services, "diagnosis_codes": facts["diagnosis_codes"]}
    auth.fhir_request = fhir_pas.build_request(order, citations, attached, letter, now())
    auth.letter = {"paragraphs": paragraphs, "attachments": auth.draft.get("attachments", [])}
    auth.submitted_at = now()
    auth.due_at = auth.submitted_at + TURNAROUND[auth.urgency]
    auth.decision = {}
    db.commit()
    log_step(db, auth, actor, f"Sent PAS request (attempt {auth.attempts}) with {len(citations)} cited passage(s) and "
                              f"{len(attached) + 1} attachment(s)", kind="decision")
    transition(db, auth, AuthStatus.SUBMITTED, actor,
               f"Submitted to {profile.name}; {'72-hour' if auth.urgency == 'urgent' else '7-day'} response clock started")
    enqueue(db, "auth_decide", None, key=f"auth_decide:{auth.id}:{auth.attempts}",
            payload={"authorization_id": auth.id, "attempt": auth.attempts}, delay_seconds=settings.payer_delay_seconds)


def handle_auth_decide(db: Session, _claim, _llm: LLM | None, payload: dict) -> None:
    """The mock payer reviews the PAS request; we read its PAS response like any other."""
    auth = db.get(Authorization, payload["authorization_id"])
    if auth.status != AuthStatus.SUBMITTED or payload.get("attempt") != auth.attempts:
        return
    request = fhir_pas.read_request(auth.fhir_request)
    review = payer.review_authorization(request, auth.payer_name, auth.attempts, now().date())
    auth.fhir_response = fhir_pas.build_response(request, review, now())
    decision = fhir_pas.read_response(auth.fhir_response)
    auth.decision = {**decision, "satisfied": review["satisfied"], "missing": review["missing"]}
    auth.decided_at = now()
    turnaround = (auth.decided_at - aware(auth.submitted_at)).total_seconds()
    db.commit()
    if decision["decision"] == "approved" and decision["auth_number"]:
        auth.auth_number = decision["auth_number"]
        folder = settings.storage_dir / f"auth_{auth.id}"
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / "authorization_determination.pdf"
        determination_letter(str(path), payer=payer.profile_for(auth.payer_name).name, auth_number=decision["auth_number"],
                             member_id=auth.member_id or "", patient=auth.patient_name or "",
                             valid_from=decision["valid_from"], valid_to=decision["valid_to"], services=auth.services,
                             request_number=auth.number)
        pdf = parse_pdf(str(path))
        db.add(Document(authorization_id=auth.id, filename=path.name, path=str(path), kind="prior_authorization",
                        pages=pdf.pages, text=pdf.text))
        db.commit()
        log_step(db, auth, GATEWAY, f"Approved: authorization {decision['auth_number']}, valid {decision['valid_from']} "
                                    f"to {decision['valid_to']} (answered in {turnaround:.0f}s)")
        target = AuthStatus.APPROVED
    elif decision["decision"] == "pended":
        log_step(db, auth, GATEWAY, f"Pended: {' '.join(decision['notes']) or decision['disposition']}")
        target = AuthStatus.PENDED
    else:
        log_step(db, auth, GATEWAY, decision["disposition"] or "Denied")
        target = AuthStatus.DENIED
    audit.record(db, GATEWAY, f"authorization.{target}", "authorization", auth.number,
                 {"auth_number": auth.auth_number, "attempt": auth.attempts, "review_action": decision["review_action"]},
                 commit=False)
    transition(db, auth, target, GATEWAY, decision["disposition"])


def add_document(db: Session, auth: Authorization, filename: str, path: str, actor: str) -> Document:
    """A clinical note addendum: it isn't extracted, it's documentation for the criteria check."""
    pdf = parse_pdf(path)
    doc = Document(authorization_id=auth.id, filename=filename, path=path, kind="clinical_note", pages=pdf.pages,
                   text=pdf.text)
    db.add(doc)
    db.commit()
    log_step(db, auth, actor, f"Added documentation: {filename}", kind="decision", document_id=doc.id)
    transition(db, auth, AuthStatus.CHECKING, actor, "Re-checking the criteria with the new documentation")
    queue_check(db, auth)
    return doc


def give_up(db: Session, payload: dict, message: str) -> None:
    auth = db.get(Authorization, payload.get("authorization_id"))
    if auth and AuthStatus.FAILED in TRANSITIONS[AuthStatus(auth.status)]:
        transition(db, auth, AuthStatus.FAILED, SYSTEM, f"Processing stopped: {message[:300]}")


HANDLERS = {"auth_read": handle_auth_read, "auth_check": handle_auth_check, "auth_decide": handle_auth_decide}
