"""Database rows -> JSON for the frontend."""

from datetime import datetime, timezone

from app.models import AgentRun, Claim, ClaimEvent, Document, Finding, User
from app.verticals.healthcare.schema import DOCUMENT_TYPES


def iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).isoformat()


def user_json(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "role": user.role}


def claim_summary(claim: Claim, finding_count: int | None = None) -> dict:
    return {
        "id": claim.id, "number": claim.number, "status": claim.status, "risk": claim.risk,
        "patient_name": claim.patient_name, "payer_name": claim.payer_name, "total_charge": claim.total_charge,
        "service_date": claim.service_date, "source": claim.source,
        "finding_count": finding_count if finding_count is not None else len(claim.findings),
        "created_at": iso(claim.created_at), "updated_at": iso(claim.updated_at),
    }


def document_json(doc: Document) -> dict:
    return {"id": doc.id, "filename": doc.filename, "kind": doc.kind, "pages": doc.pages,
            "label": DOCUMENT_TYPES.get(doc.kind or "", {}).get("label", "Unrecognised")}


def finding_json(f: Finding) -> dict:
    return {"id": f.id, "code": f.code, "severity": f.severity, "source": f.source, "title": f.title,
            "detail": f.detail, "evidence": f.evidence}


def event_json(e: ClaimEvent) -> dict:
    return {"id": e.id, "type": e.type, "actor": e.actor, "message": e.message, "data": e.data, "at": iso(e.at)}


def field_schema() -> dict:
    """Labels and order of fields per document type, so the UI doesn't hard-code them."""
    return {kind: {"label": spec["label"],
                   "fields": [{"name": f.name, "label": f.label, "type": f.type} for f in spec["fields"]],
                   "lists": spec["lists"]}
            for kind, spec in DOCUMENT_TYPES.items()}


def claim_detail(claim: Claim, runs: list[AgentRun], submission) -> dict:
    return {
        **claim_summary(claim),
        "processed_seconds": claim.processed_seconds,
        "extracted": {k: v for k, v in (claim.extracted or {}).items() if not k.startswith("_")},
        "documents": [document_json(d) for d in claim.documents],
        "findings": [finding_json(f) for f in claim.findings],
        "events": [event_json(e) for e in claim.events],
        "agent_runs": [{"agent": r.agent, "model": r.model, "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens, "latency_ms": r.latency_ms, "at": iso(r.at)} for r in runs],
        "submission": {"payer_reference": submission.payer_reference, "at": iso(submission.at)} if submission else None,
        "schema": field_schema(),
    }
