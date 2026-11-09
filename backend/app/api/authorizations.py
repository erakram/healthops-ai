"""Prior authorization requests: worklist, detail, intake, documentation, edits and submission."""

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.agents.authorization import ACTIONS
from app.api.denials import LetterEdit, _final_paragraphs
from app.api.serializers import document_json, iso
from app.authorizations import (CAN_ADD_DOCUMENTS, OPEN, SUBMITTABLE, TRANSITIONS, AuthStatus, add_document, aware,
                                log_step, submit, transition)
from app.config import settings
from app.core import audit
from app.core.auth import current_user, require_role
from app.core.workflow import enqueue
from app.db import get_db
from app.models import AgentRun, Authorization, Document, User
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.samples import get_case, list_cases

router = APIRouter(prefix="/api", tags=["authorizations"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")

LABELS = {
    "received": "Received", "reading": "Reading documents", "checking": "Checking criteria",
    "not_required": "No auth needed", "ineligible": "Coverage inactive", "needs_documentation": "Needs documentation",
    "ready": "Ready to submit", "submitted": "With payer", "approved": "Approved", "pended": "More info requested",
    "denied": "Denied", "withdrawn": "Withdrawn", "failed": "Failed",
}
WAITING = {AuthStatus.SUBMITTED}
DONE = {AuthStatus.APPROVED, AuthStatus.DENIED, AuthStatus.NOT_REQUIRED, AuthStatus.INELIGIBLE, AuthStatus.WITHDRAWN}


def _hours_left(due: datetime | None) -> float | None:
    return round((aware(due) - datetime.now(timezone.utc)).total_seconds() / 3600, 1) if due else None


def auth_summary(a: Authorization) -> dict:
    turnaround = (aware(a.decided_at) - aware(a.submitted_at)).total_seconds() if a.decided_at and a.submitted_at else None
    return {
        "id": a.id, "number": a.number, "status": a.status, "status_label": LABELS.get(a.status, a.status),
        "urgency": a.urgency, "source": a.source, "patient_name": a.patient_name, "payer_name": a.payer_name,
        "member_id": a.member_id, "services": a.services, "scheduled_date": a.scheduled_date,
        "action": (a.assessment or {}).get("action"), "action_label": ACTIONS.get((a.assessment or {}).get("action") or ""),
        "missing": len((a.assessment or {}).get("missing") or []), "attempts": a.attempts, "auth_number": a.auth_number,
        "submitted_at": iso(a.submitted_at), "due_at": iso(a.due_at),
        "hours_left": _hours_left(a.due_at) if a.status == AuthStatus.SUBMITTED else None,
        "decided_at": iso(a.decided_at), "turnaround_seconds": round(turnaround, 1) if turnaround is not None else None,
        "has_sample_addendum": _sample_addendum(a) is not None and a.status in CAN_ADD_DOCUMENTS,
        "created_at": iso(a.created_at), "updated_at": iso(a.updated_at),
    }


def _sample_addendum(a: Authorization) -> Path | None:
    if not a.case_id:
        return None
    case = get_case(a.case_id)
    path = Path(case["path"]) / "extra" / "addendum_note.pdf" if case else None
    if not path or not path.exists():
        return None
    if any(d.filename == "addendum_note.pdf" for d in a.documents):
        return None
    return path


def _load(db: Session, auth_id: int) -> Authorization:
    auth = db.scalar(select(Authorization).where(Authorization.id == auth_id)
                     .options(selectinload(Authorization.documents), selectinload(Authorization.events)))
    if not auth:
        raise HTTPException(404, "Authorization request not found")
    return auth


def detail(db: Session, a: Authorization) -> dict:
    runs = db.scalars(select(AgentRun).where(AgentRun.authorization_id == a.id).order_by(AgentRun.id)).all()
    return {
        **auth_summary(a),
        "extracted": a.extracted, "requirement": a.requirement, "assessment": a.assessment, "draft": a.draft,
        "letter": a.letter, "decision": a.decision, "fhir_request": a.fhir_request, "fhir_response": a.fhir_response,
        "policy": get_policy((a.assessment or {}).get("policy_id") or (a.requirement or {}).get("policy_id")),
        "documents": [{**document_json(d), "addendum": d.kind == "clinical_note" and not d.fields} for d in a.documents],
        "events": [{"id": e.id, "type": e.type, "actor": e.actor, "message": e.message, "data": e.data, "at": iso(e.at)}
                   for e in a.events],
        "agent_runs": [{"agent": r.agent, "model": r.model, "input_tokens": r.input_tokens,
                        "output_tokens": r.output_tokens, "latency_ms": r.latency_ms} for r in runs],
        "actions": ACTIONS,
    }


@router.get("/authorizations")
def list_authorizations(view: str = Query("open", pattern="^(open|waiting|done|all)$"), db: Session = Depends(get_db),
                        _: User = Depends(current_user)):
    query = select(Authorization).options(selectinload(Authorization.documents)).order_by(Authorization.id.desc())
    states = {"open": OPEN, "waiting": WAITING, "done": DONE}.get(view)
    if states:
        query = query.where(Authorization.status.in_([str(s) for s in states]))
    rows = [auth_summary(a) for a in db.scalars(query)]
    if view == "waiting":
        rows.sort(key=lambda r: r["hours_left"] if r["hours_left"] is not None else 1e9)
    return rows


@router.get("/authorizations/summary")
def authorizations_summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    auths = db.scalars(select(Authorization)).all()
    decided = [a for a in auths if a.status in (AuthStatus.APPROVED, AuthStatus.DENIED) and a.decided_at]
    first_pass = [a for a in decided if a.attempts == 1]
    turnaround = [(aware(a.decided_at) - aware(a.submitted_at)).total_seconds() for a in decided if a.submitted_at]
    return {
        "open": sum(1 for a in auths if a.status in OPEN),
        "needs_documentation": sum(1 for a in auths if a.status in (AuthStatus.NEEDS_DOCUMENTATION, AuthStatus.PENDED)),
        "with_payer": sum(1 for a in auths if a.status == AuthStatus.SUBMITTED),
        "approved": sum(1 for a in auths if a.status == AuthStatus.APPROVED),
        "approval_rate": round(100 * sum(a.status == AuthStatus.APPROVED for a in decided) / len(decided), 1) if decided else None,
        "first_pass_approval_rate": round(100 * sum(a.status == AuthStatus.APPROVED for a in first_pass) / len(first_pass), 1)
        if first_pass else None,
        "avg_turnaround_seconds": round(sum(turnaround) / len(turnaround), 1) if turnaround else None,
        "not_required": sum(1 for a in auths if a.status == AuthStatus.NOT_REQUIRED),
    }


@router.get("/auth-samples")
def auth_samples(_: User = Depends(current_user)):
    return [{"case_id": c["case_id"], "title": c["title"], "scenario": c["scenario"], "patient": c["patient"],
             "files": c["files"], "has_addendum": c["pa"]["addendum"]} for c in list_cases("pa_")]


def _new(db: Session, user: User, source: str, case_id: str | None = None) -> Authorization:
    auth = Authorization(number="pending", status=AuthStatus.RECEIVED, source=source, case_id=case_id, created_by=user.id)
    db.add(auth)
    db.flush()
    auth.number = f"PAR-{40000 + auth.id}"
    return auth


def _start(db: Session, auth: Authorization, user: User, files: list[str]) -> None:
    db.commit()
    log_step(db, auth, user.email, f"Received {len(files)} document(s)", kind="transition", to=AuthStatus.RECEIVED)
    audit.record(db, user.email, "authorization.created", "authorization", auth.number,
                 {"files": files, "source": auth.source})
    enqueue(db, "auth_read", None, key=f"auth_read:{auth.id}", payload={"authorization_id": auth.id})


@router.post("/auth-samples/{case_id}/load", status_code=201)
def load_auth_sample(case_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    case = get_case(case_id)
    if not case or not case_id.startswith("pa_"):
        raise HTTPException(404, "Sample not found")
    auth = _new(db, user, "sample", case_id)
    folder = settings.storage_dir / f"auth_{auth.id}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in case["files"]:
        shutil.copy(Path(case["path"]) / name, folder / name)
        db.add(Document(authorization_id=auth.id, filename=name, path=str(folder / name)))
    _start(db, auth, user, case["files"])
    return auth_summary(_load(db, auth.id))


async def _save_pdfs(files: list[UploadFile], folder: Path) -> list[tuple[str, Path]]:
    if not files or len(files) > 10:
        raise HTTPException(400, "Upload between 1 and 10 PDF files")
    saved = []
    for upload in files:
        data = await upload.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, f"{upload.filename} is not a PDF")
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(400, f"{upload.filename} is larger than {settings.max_upload_mb:g} MB")
        name = _SAFE_NAME.sub("_", Path(upload.filename or "document.pdf").name)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        saved.append((name, folder / name))
    return saved


@router.post("/authorizations", status_code=201)
async def create_authorization(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                               user: User = Depends(require_role("intake", "biller"))):
    auth = _new(db, user, "upload")
    saved = await _save_pdfs(files, settings.storage_dir / f"auth_{auth.id}")
    for name, path in saved:
        db.add(Document(authorization_id=auth.id, filename=name, path=str(path)))
    _start(db, auth, user, [n for n, _ in saved])
    return auth_summary(_load(db, auth.id))


@router.get("/authorizations/{auth_id}")
def get_authorization(auth_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return detail(db, _load(db, auth_id))


def _check_can_add(auth: Authorization) -> None:
    if auth.status not in CAN_ADD_DOCUMENTS:
        raise HTTPException(409, f"Documentation can't be added while the request is {LABELS[auth.status].lower()}")


@router.post("/authorizations/{auth_id}/documents")
async def add_documentation(auth_id: int, files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                            user: User = Depends(require_role("intake", "biller"))):
    auth = _load(db, auth_id)
    _check_can_add(auth)
    saved = await _save_pdfs(files[:1], settings.storage_dir / f"auth_{auth.id}" / "addenda")
    name, path = saved[0]
    add_document(db, auth, name, str(path), user.email)
    return detail(db, _load(db, auth_id))


@router.post("/authorizations/{auth_id}/sample-addendum")
def add_sample_addendum(auth_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    """Demo shortcut: the physician's addendum that comes with some sample packets."""
    auth = _load(db, auth_id)
    _check_can_add(auth)
    source = _sample_addendum(auth)
    if source is None:
        raise HTTPException(404, "This request has no sample addendum")
    folder = settings.storage_dir / f"auth_{auth.id}" / "addenda"
    folder.mkdir(parents=True, exist_ok=True)
    shutil.copy(source, folder / source.name)
    add_document(db, auth, source.name, str(folder / source.name), user.email)
    return detail(db, _load(db, auth_id))


@router.put("/authorizations/{auth_id}/letter")
def save_letter(auth_id: int, body: LetterEdit, db: Session = Depends(get_db),
                user: User = Depends(require_role("intake", "biller"))):
    auth = _load(db, auth_id)
    if auth.status not in SUBMITTABLE or not auth.draft.get("paragraphs"):
        raise HTTPException(409, "There's no draft to edit")
    auth.letter = {"paragraphs": _final_paragraphs(auth.draft, body.paragraphs), "attachments": auth.draft.get("attachments", [])}
    db.commit()
    return detail(db, _load(db, auth_id))


class Submission(LetterEdit):
    note: str | None = Field(default=None, max_length=1000)


@router.post("/authorizations/{auth_id}/submit")
def submit_request(auth_id: int, body: Submission, db: Session = Depends(get_db),
                   user: User = Depends(require_role("intake", "biller"))):
    auth = _load(db, auth_id)
    if auth.status not in SUBMITTABLE:
        raise HTTPException(409, f"The request is {LABELS[auth.status].lower()}, it can't be submitted")
    if not auth.draft.get("paragraphs"):
        raise HTTPException(409, "There's no drafted request to send")
    if auth.status == AuthStatus.NEEDS_DOCUMENTATION and not (body.note or "").strip():
        raise HTTPException(400, "Documentation is missing: say why you're submitting anyway")
    paragraphs = _final_paragraphs(auth.draft, body.paragraphs)
    if body.note:
        log_step(db, auth, user.email, f"Submitted despite missing documentation: {body.note}", kind="decision")
    audit.record(db, user.email, "authorization.submitted", "authorization", auth.number,
                 {"attempt": auth.attempts + 1, "paragraphs_edited": sum(p["edited"] for p in paragraphs),
                  "missing": [m["id"] for m in (auth.assessment or {}).get("missing", [])], "note": body.note})
    submit(db, auth, user.email, paragraphs)
    return detail(db, _load(db, auth_id))


class Note(BaseModel):
    note: str = Field(min_length=1, max_length=1000)


@router.post("/authorizations/{auth_id}/withdraw")
def withdraw(auth_id: int, body: Note, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    auth = _load(db, auth_id)
    if AuthStatus.WITHDRAWN not in TRANSITIONS[AuthStatus(auth.status)]:
        raise HTTPException(409, "This request can't be withdrawn now")
    audit.record(db, user.email, "authorization.withdrawn", "authorization", auth.number, {"note": body.note}, commit=False)
    transition(db, auth, AuthStatus.WITHDRAWN, user.email, body.note)
    return detail(db, _load(db, auth_id))


@router.post("/authorizations/{auth_id}/retry")
def retry(auth_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    auth = _load(db, auth_id)
    if auth.status != AuthStatus.FAILED:
        raise HTTPException(409, "Only failed requests can be retried")
    for doc in auth.documents:
        if doc.fields:  # re-read what the Document Agent read; keep addenda as they are
            doc.kind, doc.fields = None, {}
    transition(db, auth, AuthStatus.READING, user.email, "Retry requested")
    enqueue(db, "auth_read", None, key=f"auth_read:{auth.id}:retry:{len(auth.events)}", payload={"authorization_id": auth.id})
    return detail(db, _load(db, auth_id))
