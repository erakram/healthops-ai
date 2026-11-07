import re
import shutil
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import claim_detail, claim_summary, iso
from app.config import settings
from app.core import audit
from app.core.auth import current_user, require_role
from app.core.workflow import AWAITING_HUMAN, InvalidTransition, Status, enqueue, transition
from app.db import get_db
from app.documents.pdf import render_page_png
from app.models import AgentRun, Claim, ClaimEvent, Correction, Denial, Document, Finding, Remittance, Submission, User
from app.verticals.healthcare.samples import get_case, list_cases
from app.verticals.healthcare.schema import DOCUMENT_TYPES, normalize_value

router = APIRouter(prefix="/api", tags=["claims"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _load_claim(db: Session, claim_id: int) -> Claim:
    claim = db.scalar(select(Claim).where(Claim.id == claim_id).options(
        selectinload(Claim.documents), selectinload(Claim.findings), selectinload(Claim.events)))
    if not claim:
        raise HTTPException(404, "Claim not found")
    return claim


def _detail(db: Session, claim: Claim) -> dict:
    from app.api.denials import denial_summary, remittance_json

    runs = db.scalars(select(AgentRun).where(AgentRun.claim_id == claim.id).order_by(AgentRun.id)).all()
    submissions = db.scalars(select(Submission).where(Submission.claim_id == claim.id).order_by(Submission.id)).all()
    remits = db.scalars(select(Remittance).where(Remittance.claim_id == claim.id).order_by(Remittance.id)).all()
    denials = db.scalars(select(Denial).where(Denial.claim_id == claim.id).order_by(Denial.id)).all()
    detail = claim_detail(claim, runs, submissions[0] if submissions else None)
    allowed = sum(l.get("allowed") or 0 for r in remits for l in r.parsed["claim"]["lines"])
    detail["submissions"] = [{"payer_reference": s.payer_reference, "frequency": s.frequency, "at": iso(s.at)}
                             for s in submissions]
    detail["remittances"] = [remittance_json(r) for r in remits]
    detail["denials"] = [denial_summary(d) for d in denials]
    detail["financials"] = {
        "billed": claim.total_charge, "allowed": round(allowed, 2),
        "paid": round(sum(r.paid for r in remits), 2),
        "patient_responsibility": round(sum(d.amount for d in denials if d.group_code == "PR"
                                            or d.status == "patient_billed"), 2),
        "recovered": round(sum(d.recovered for d in denials), 2),
        "written_off": round(sum(d.amount for d in denials if d.status == "written_off"), 2),
    } if remits else None
    return detail


def _new_claim(db: Session, user: User, source: str) -> Claim:
    claim = Claim(number="pending", status=Status.RECEIVED, source=source, created_by=user.id)
    db.add(claim)
    db.flush()
    claim.number = f"CLM-{30000 + claim.id}"
    return claim


def _start_pipeline(db: Session, claim: Claim, user: User, files: list[str]) -> None:
    db.add(ClaimEvent(claim_id=claim.id, type="transition", actor=user.email,
                      message=f"Received {len(files)} document(s)", data={"to": Status.RECEIVED, "files": files}))
    audit.record(db, user.email, "claim.created", "claim", claim.number, {"files": files, "source": claim.source},
                 commit=False)
    db.commit()
    enqueue(db, "extract", claim)


@router.get("/claims")
def list_claims(status: str | None = None, q: str | None = None, risk: str | None = None,
                limit: int = Query(200, le=500), db: Session = Depends(get_db), _: User = Depends(current_user)):
    counts = select(Finding.claim_id, func.count(Finding.id).label("n")).group_by(Finding.claim_id).subquery()
    query = (select(Claim, func.coalesce(counts.c.n, 0)).outerjoin(counts, counts.c.claim_id == Claim.id)
             .order_by(Claim.created_at.desc()).limit(limit))
    if status:
        query = query.where(Claim.status.in_(status.split(",")))
    if risk:
        query = query.where(Claim.risk == risk)
    if q:
        like = f"%{q.strip()}%"
        query = query.where(or_(Claim.number.ilike(like), Claim.patient_name.ilike(like), Claim.payer_name.ilike(like)))
    return [claim_summary(claim, n) for claim, n in db.execute(query).all()]


@router.post("/claims", status_code=201)
async def create_claim(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                       user: User = Depends(require_role("intake", "biller"))):
    if not files or len(files) > 10:
        raise HTTPException(400, "Upload between 1 and 10 PDF files")
    contents = []
    for upload in files:
        data = await upload.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, f"{upload.filename} is not a PDF")
        if len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(400, f"{upload.filename} is larger than {settings.max_upload_mb:g} MB")
        contents.append((_SAFE_NAME.sub("_", Path(upload.filename or "document.pdf").name), data))

    claim = _new_claim(db, user, "upload")
    folder = settings.storage_dir / f"claim_{claim.id}"
    folder.mkdir(parents=True, exist_ok=True)
    for name, data in contents:
        path = folder / name
        path.write_bytes(data)
        db.add(Document(claim_id=claim.id, filename=name, path=str(path)))
    _start_pipeline(db, claim, user, [n for n, _ in contents])
    return claim_summary(_load_claim(db, claim.id), 0)


@router.get("/samples")
def samples(_: User = Depends(current_user)):
    """case_* packets show problems before submission; den_* are clean claims the payer
    will deny (or pay) after submission, for the denials workflow."""
    return [{"case_id": c["case_id"], "title": c["title"], "scenario": c["scenario"], "patient": c["patient"],
             "files": c["files"], "stage": "after_submission" if c["case_id"].startswith("den_") else "intake"}
            for c in list_cases("case_") + list_cases("den_")]


@router.post("/samples/{case_id}/load", status_code=201)
def load_sample(case_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    """Create a claim from a sample packet and run it through the real pipeline."""
    case = get_case(case_id)
    if not case or not case_id.startswith(("case_", "den_")):
        raise HTTPException(404, "Sample not found")
    claim = _new_claim(db, user, "sample")
    folder = settings.storage_dir / f"claim_{claim.id}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in case["files"]:
        shutil.copy(Path(case["path"]) / name, folder / name)
        db.add(Document(claim_id=claim.id, filename=name, path=str(folder / name)))
    _start_pipeline(db, claim, user, case["files"])
    return claim_summary(_load_claim(db, claim.id), 0)


@router.get("/claims/{claim_id}")
def get_claim(claim_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return _detail(db, _load_claim(db, claim_id))


class Decision(BaseModel):
    action: Literal["approve", "reject"]
    note: str | None = None


@router.post("/claims/{claim_id}/decision")
def decide(claim_id: int, body: Decision, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    claim = _load_claim(db, claim_id)
    if Status(claim.status) not in AWAITING_HUMAN:
        raise HTTPException(409, f"Claim is {claim.status.replace('_', ' ')}, not waiting for a decision")
    if body.action == "reject" and not (body.note or "").strip():
        raise HTTPException(400, "Say why the claim is rejected")
    target = Status.APPROVED if body.action == "approve" else Status.REJECTED
    db.add(ClaimEvent(claim_id=claim.id, type="decision", actor=user.email,
                      message=f"{user.name} {'approved' if target == Status.APPROVED else 'rejected'} the claim",
                      data={"note": body.note, "open_findings": len(claim.findings)}))
    audit.record(db, user.email, f"claim.{body.action}", "claim", claim.number,
                 {"note": body.note, "findings": [f.code for f in claim.findings], "risk": claim.risk}, commit=False)
    transition(db, claim, target, user.email, body.note)
    if target == Status.APPROVED:
        enqueue(db, "submit", claim)
    return _detail(db, _load_claim(db, claim_id))


class FieldCorrection(BaseModel):
    document_type: str
    field: str
    value: str


@router.patch("/claims/{claim_id}/fields")
def correct_field(claim_id: int, body: FieldCorrection, db: Session = Depends(get_db),
                  user: User = Depends(require_role("biller", "intake"))):
    """A reviewer fixes a value the agent read wrongly. The claim is validated again,
    and the correction is kept as a future test case."""
    claim = _load_claim(db, claim_id)
    if Status(claim.status) not in AWAITING_HUMAN:
        raise HTTPException(409, "Fields can only be corrected while the claim is waiting for review")
    spec = next((f for f in DOCUMENT_TYPES.get(body.document_type, {}).get("fields", []) if f.name == body.field), None)
    if not spec:
        raise HTTPException(400, "Unknown field")

    extracted = dict(claim.extracted or {})
    doc_fields = dict(extracted.get(body.document_type) or {})
    old = (doc_fields.get(body.field) or {}).get("value")
    doc_fields[body.field] = {**(doc_fields.get(body.field) or {}), "value": normalize_value(spec.type, body.value),
                              "raw": body.value, "confidence": 1.0, "corrected_by": user.email}
    extracted[body.document_type] = doc_fields
    claim.extracted = extracted
    if body.document_type == "claim_form":
        value = doc_fields[body.field]["value"]
        if body.field == "patient_name":
            claim.patient_name = value
        elif body.field == "total_charge" and isinstance(value, (int, float)):
            claim.total_charge = value
        elif body.field == "service_date":
            claim.service_date = value
    db.add(Correction(claim_id=claim.id, field=f"{body.document_type}.{body.field}",
                      old_value=None if old is None else str(old), new_value=body.value, user_id=user.id))
    db.add(ClaimEvent(claim_id=claim.id, type="correction", actor=user.email,
                      message=f"Corrected {spec.label.lower()}: '{old}' → '{body.value}'"))
    audit.record(db, user.email, "claim.field_corrected", "claim", claim.number,
                 {"field": f"{body.document_type}.{body.field}", "old": None if old is None else str(old),
                  "new": body.value}, commit=False)
    transition(db, claim, Status.VALIDATING, user.email, "Re-validating after a correction")
    enqueue(db, "validate", claim, key=f"validate:{claim.id}:correction:{db.scalar(select(func.count(Correction.id)))}")
    return _detail(db, _load_claim(db, claim_id))


@router.post("/claims/{claim_id}/retry")
def retry(claim_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("intake", "biller"))):
    claim = _load_claim(db, claim_id)
    if claim.status != Status.FAILED:
        raise HTTPException(409, "Only failed claims can be retried")
    for doc in claim.documents:  # a manual retry re-reads everything
        doc.kind, doc.fields = None, {}
    try:
        transition(db, claim, Status.EXTRACTING, user.email, "Retry requested")
    except InvalidTransition as exc:
        raise HTTPException(409, str(exc))
    enqueue(db, "extract", claim, key=f"extract:{claim.id}:retry:{len(claim.events)}")
    return _detail(db, _load_claim(db, claim_id))


@router.get("/documents/{document_id}/pages/{page}.png")
def page_image(document_id: int, page: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    try:
        png = render_page_png(doc.path, page)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})


@router.get("/documents/{document_id}/file")
def document_file(document_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(404, "Document not found")
    return FileResponse(doc.path, media_type="application/pdf", filename=doc.filename)
