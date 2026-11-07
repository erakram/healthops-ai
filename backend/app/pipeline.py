"""The claim pipeline: one handler per job kind.

    extract  -> read every document, build the claim's extracted record
    validate -> rules + AI review, write findings, hand the claim to a person
    submit   -> send an approved claim to the (mock) payer, exactly once

Handlers are safe to re-run: each one rebuilds its output from the documents rather
than appending to it, so a retried job gives the same result.
"""

import secrets
import time
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.agents.extraction import extract_document
from app.agents.reviewer import review_claim
from app.config import settings
from app.core import audit
from app.core.llm import LLM, LLMError, LLMResult
from app.core.workflow import Status, enqueue, log_step, transition
from app.documents.pdf import locate, parse_pdf
from app.models import AgentRun, Claim, Finding, Submission
from app.verticals.healthcare.rules import SEVERITY_ORDER, RuleFinding, risk_level
from app.verticals.healthcare.schema import DOCUMENT_TYPES

DOCUMENT_AGENT = "Document Agent"
REVIEW_AGENT = "Claims Review Agent"
SYSTEM = "Workflow Engine"


def _record_run(db: Session, claim: Claim, agent: str, result: LLMResult | None) -> None:
    if result:
        db.add(AgentRun(claim_id=claim.id, agent=agent, model=result.model, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, latency_ms=result.latency_ms))


def handle_extract(db: Session, claim: Claim, llm: LLM | None) -> None:
    if llm is None:
        raise LLMError("No LLM configured: set GROQ_API_KEY to process uploaded claims")
    if claim.status != Status.EXTRACTING:
        transition(db, claim, Status.EXTRACTING, SYSTEM, "Document Agent started")

    record: dict = {}
    security: list[dict] = []
    for doc in claim.documents:
        # Each document is committed as soon as it's read, so a retry (after a rate limit
        # or a crash) continues with the next unread document instead of starting over.
        if doc.kind is None:
            log_step(db, claim, DOCUMENT_AGENT, f"Reading {doc.filename}", document_id=doc.id)
            parsed = parse_pdf(doc.path)
            result = extract_document(llm, parsed, doc.id, model=settings.extraction_model)
            _record_run(db, claim, DOCUMENT_AGENT, result.llm)
            suspicious = None
            if result.suspicious_text:
                hit = locate(parsed, result.suspicious_text)
                suspicious = {"document_id": doc.id, "filename": doc.filename, "text": result.suspicious_text,
                              "evidence": {"document_id": doc.id, **hit} if hit else None}
            doc.pages, doc.text = parsed.pages, parsed.text
            doc.kind, doc.fields = result.document_type, {**result.fields, "_suspicious": suspicious}
            db.commit()

            label = DOCUMENT_TYPES.get(result.document_type, {}).get("label", "Unrecognised document")
            grounded = sum(1 for f in result.fields.values() if f.get("evidence"))
            log_step(db, claim, DOCUMENT_AGENT,
                     f"{doc.filename} is a {label.lower()}: {len(result.fields)} fields read, {grounded} matched to the page",
                     document_id=doc.id, kind=result.document_type)
            if suspicious:
                log_step(db, claim, DOCUMENT_AGENT, f"Ignored instructions embedded in {doc.filename}", document_id=doc.id)

        fields = {k: v for k, v in (doc.fields or {}).items() if not k.startswith("_")}
        if (doc.fields or {}).get("_suspicious"):
            security.append(doc.fields["_suspicious"])
        if doc.kind in DOCUMENT_TYPES and doc.kind not in record:
            record[doc.kind] = fields

    claim.extracted = {**record, "_security": security}
    claim_form = record.get("claim_form", {})
    claim.patient_name = (claim_form.get("patient_name") or {}).get("value")
    claim.payer_name = (claim_form.get("payer_name") or {}).get("value")
    total = (claim_form.get("total_charge") or {}).get("value")
    claim.total_charge = total if isinstance(total, (int, float)) else None
    claim.service_date = (claim_form.get("service_date") or {}).get("value")
    db.commit()

    transition(db, claim, Status.VALIDATING, SYSTEM, "Extraction finished, validating")
    enqueue(db, "validate", claim, key=f"validate:{claim.id}:{time.time_ns()}")


def _other_claims_for_duplicates(db: Session, claim: Claim) -> list[dict]:
    others = []
    rows = db.scalars(select(Claim).where(Claim.id != claim.id, Claim.status != Status.REJECTED)
                      .order_by(Claim.id.desc()).limit(500))
    for other in rows:
        form = (other.extracted or {}).get("claim_form") or {}
        others.append({
            "number": other.number,
            "member_id": (form.get("member_id") or {}).get("value"),
            "service_date": other.service_date,
            "cpt_codes": [i.get("cpt") for i in (form.get("line_items") or {}).get("value", [])],
        })
    return others


def handle_validate(db: Session, claim: Claim, llm: LLM | None) -> None:
    record = {k: v for k, v in (claim.extracted or {}).items() if not k.startswith("_")}
    note_doc = next((d for d in claim.documents if d.kind == "clinical_note"), None)
    note = (note_doc.id, parse_pdf(note_doc.path)) if note_doc else None

    log_step(db, claim, REVIEW_AGENT, "Running claim rules: identity, coverage, amounts, codes, authorization")
    review = review_claim(record, _other_claims_for_duplicates(db, claim), llm, note, model=settings.review_model)
    _record_run(db, claim, REVIEW_AGENT, review.llm)
    if review.llm:
        ai_count = sum(1 for f in review.findings if f.source == "ai")
        log_step(db, claim, REVIEW_AGENT, f"Compared the clinical note with billed services: {ai_count} issue(s)"
                 + (f", {review.dropped_ai_findings} unsupported suggestion(s) discarded" if review.dropped_ai_findings else ""))

    findings = list(review.findings)
    for item in (claim.extracted or {}).get("_security", []):
        findings.insert(0, RuleFinding("PROMPT-INJECTION", "critical", f"{item['filename']} contains instructions aimed at the AI",
                                       f"The document says: \"{item['text'][:200]}\". It was treated as data and ignored, "
                                       "but someone should check where this document came from.",
                                       [item["evidence"]] if item.get("evidence") else []))

    db.execute(delete(Finding).where(Finding.claim_id == claim.id))
    for f in sorted(findings, key=lambda f: SEVERITY_ORDER[f.severity]):
        db.add(Finding(claim_id=claim.id, code=f.code, severity=f.severity, source=f.source,
                       title=f.title, detail=f.detail, evidence=f.evidence))
    claim.risk = risk_level(findings)
    if claim.processed_seconds is None:
        created = claim.created_at if claim.created_at.tzinfo else claim.created_at.replace(tzinfo=timezone.utc)
        claim.processed_seconds = round((datetime.now(timezone.utc) - created).total_seconds(), 1)
    db.commit()

    needs_attention = [f for f in findings if f.severity != "low"]
    log_step(db, claim, REVIEW_AGENT, f"{len(findings)} finding(s), risk {claim.risk}")
    transition(db, claim, Status.NEEDS_REVIEW if needs_attention else Status.READY, SYSTEM,
               "Waiting for a biller to review the findings" if needs_attention
               else "Only minor notes, ready for approval" if findings else "No issues found, ready for approval")


def handle_submit(db: Session, claim: Claim, _llm: LLM | None) -> None:
    """Mock payer submission. The unique key means a retried job reuses the first submission."""
    key = f"submit:{claim.id}"
    submission = db.scalar(select(Submission).where(Submission.idempotency_key == key))
    if not submission:
        submission = Submission(claim_id=claim.id, idempotency_key=key, payer_reference=f"PYR-{secrets.token_hex(4).upper()}")
        db.add(submission)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            submission = db.scalar(select(Submission).where(Submission.idempotency_key == key))
        audit.record(db, SYSTEM, "claim.submitted", "claim", claim.number, {"payer_reference": submission.payer_reference})
    log_step(db, claim, SYSTEM, f"Submitted to {claim.payer_name or 'payer'}, reference {submission.payer_reference}")
    transition(db, claim, Status.SUBMITTED, SYSTEM, f"Accepted by payer gateway ({submission.payer_reference})")


HANDLERS = {"extract": handle_extract, "validate": handle_validate, "submit": handle_submit}
