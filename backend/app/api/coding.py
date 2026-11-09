"""Medical coding: encounter worklist, coder workbench, code search, finalize and claim generation."""

import re
import shutil
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api.serializers import document_json, iso
from app.coding import EncStatus, create_claim, finalize, log_step, transition, validate_codes
from app.config import settings
from app.core import audit
from app.core.auth import current_user, require_role
from app.core.workflow import enqueue
from app.db import get_db
from app.models import AgentRun, Document, Encounter, User
from app.verticals.healthcare.codesets import CPT_CODES, search_cpt, search_icd10
from app.verticals.healthcare.samples import get_case, list_cases

router = APIRouter(prefix="/api", tags=["coding"])
_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
LABELS = {"received": "Received", "reading": "Reading documents", "coding": "Coding", "review": "Needs coder review",
          "coded": "Coded", "billed": "Claim created", "failed": "Failed"}
OPEN = {EncStatus.RECEIVED, EncStatus.READING, EncStatus.CODING, EncStatus.REVIEW, EncStatus.FAILED}


def summary(e: Encounter) -> dict:
    s = e.suggestion or {}
    final = e.final or {}
    return {
        "id": e.id, "number": e.number, "status": e.status, "status_label": LABELS.get(e.status, e.status),
        "source": e.source, "patient_name": e.patient_name, "payer_name": e.payer_name, "provider_name": e.provider_name,
        "service_date": e.service_date,
        "codes": [d["code"] for d in final.get("icd") or s.get("diagnoses") or []]
                 + [p["code"] + ("-" + "-".join(p["modifiers"]) if p.get("modifiers") else "")
                    for p in final.get("cpt") or s.get("procedures") or []],
        "edits": len(s.get("edits") or []), "agreed": final.get("agreed"), "changes": len(final.get("changes") or []),
        "claim_id": e.claim_id, "coded_by": e.coded_by, "coded_at": iso(e.coded_at), "created_at": iso(e.created_at),
    }


def _load(db: Session, enc_id: int) -> Encounter:
    enc = db.scalar(select(Encounter).where(Encounter.id == enc_id)
                    .options(selectinload(Encounter.documents), selectinload(Encounter.events)))
    if not enc:
        raise HTTPException(404, "Encounter not found")
    return enc


def detail(db: Session, e: Encounter) -> dict:
    runs = db.scalars(select(AgentRun).where(AgentRun.encounter_id == e.id)).all()
    return {**summary(e), "extracted": e.extracted, "suggestion": e.suggestion, "final": e.final,
            "documents": [document_json(d) for d in e.documents],
            "events": [{"id": v.id, "type": v.type, "actor": v.actor, "message": v.message, "data": v.data, "at": iso(v.at)}
                       for v in e.events],
            "agent_runs": [{"agent": r.agent, "model": r.model, "input_tokens": r.input_tokens,
                            "output_tokens": r.output_tokens, "latency_ms": r.latency_ms} for r in runs]}


@router.get("/encounters")
def list_encounters(view: str = Query("open", pattern="^(open|done|all)$"), db: Session = Depends(get_db),
                    _: User = Depends(current_user)):
    query = select(Encounter).order_by(Encounter.id.desc())
    if view == "open":
        query = query.where(Encounter.status.in_([str(s) for s in OPEN]))
    elif view == "done":
        query = query.where(Encounter.status.in_([EncStatus.CODED, EncStatus.BILLED]))
    return [summary(e) for e in db.scalars(query)]


@router.get("/encounters/summary")
def encounters_summary(db: Session = Depends(get_db), _: User = Depends(current_user)):
    encs = db.scalars(select(Encounter)).all()
    coded = [e for e in encs if e.final]
    return {"review": sum(1 for e in encs if e.status == EncStatus.REVIEW),
            "coded": len(coded), "billed": sum(1 for e in encs if e.status == EncStatus.BILLED),
            "agreement_rate": round(100 * sum(1 for e in coded if e.final.get("agreed")) / len(coded), 1) if coded else None,
            "edits_raised": sum(len((e.suggestion or {}).get("edits") or []) for e in encs)}


@router.get("/codes/icd10")
def icd10_search(q: str = Query(min_length=1, max_length=200), _: User = Depends(current_user)):
    return search_icd10(q, 15)


@router.get("/codes/cpt")
def cpt_list(q: str | None = None, _: User = Depends(current_user)):
    if q:
        return search_cpt(q, 10)
    return [{"code": c, "description": d, "price": p, "max_units": m, "em": em} for c, (d, p, m, em) in CPT_CODES.items()]


@router.get("/coding-samples")
def coding_samples(_: User = Depends(current_user)):
    return [{"case_id": c["case_id"], "title": c["title"], "scenario": c["scenario"], "patient": c["patient"],
             "files": c["files"]} for c in list_cases("enc_")]


def _new(db: Session, user: User, source: str, case_id: str | None = None) -> Encounter:
    enc = Encounter(number="pending", status=EncStatus.RECEIVED, source=source, case_id=case_id, created_by=user.id)
    db.add(enc)
    db.flush()
    enc.number = f"ENC-{50000 + enc.id}"
    return enc


def _start(db: Session, enc: Encounter, user: User, files: list[str]) -> None:
    db.commit()
    log_step(db, enc, user.email, f"Received {len(files)} document(s)", kind="transition")
    audit.record(db, user.email, "encounter.created", "encounter", enc.number, {"files": files, "source": enc.source})
    enqueue(db, "enc_read", None, key=f"enc_read:{enc.id}", payload={"encounter_id": enc.id})


@router.post("/coding-samples/{case_id}/load", status_code=201)
def load_sample(case_id: str, db: Session = Depends(get_db), user: User = Depends(require_role("biller", "intake"))):
    case = get_case(case_id)
    if not case or not case_id.startswith("enc_"):
        raise HTTPException(404, "Sample not found")
    enc = _new(db, user, "sample", case_id)
    folder = settings.storage_dir / f"enc_{enc.id}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in case["files"]:
        shutil.copy(Path(case["path"]) / name, folder / name)
        db.add(Document(encounter_id=enc.id, filename=name, path=str(folder / name)))
    _start(db, enc, user, case["files"])
    return summary(_load(db, enc.id))


@router.post("/encounters", status_code=201)
async def create_encounter(files: list[UploadFile] = File(...), db: Session = Depends(get_db),
                           user: User = Depends(require_role("biller", "intake"))):
    if not files or len(files) > 5:
        raise HTTPException(400, "Upload the visit note and insurance card (up to 5 PDFs)")
    enc = _new(db, user, "upload")
    folder = settings.storage_dir / f"enc_{enc.id}"
    folder.mkdir(parents=True, exist_ok=True)
    names = []
    for upload in files:
        data = await upload.read()
        if not data.startswith(b"%PDF") or len(data) > settings.max_upload_mb * 1024 * 1024:
            raise HTTPException(400, f"{upload.filename} isn't a PDF under {settings.max_upload_mb:g} MB")
        name = _SAFE_NAME.sub("_", Path(upload.filename or "document.pdf").name)
        (folder / name).write_bytes(data)
        db.add(Document(encounter_id=enc.id, filename=name, path=str(folder / name)))
        names.append(name)
    _start(db, enc, user, names)
    return summary(_load(db, enc.id))


@router.get("/encounters/{enc_id}")
def get_encounter(enc_id: int, db: Session = Depends(get_db), _: User = Depends(current_user)):
    return detail(db, _load(db, enc_id))


class CptLine(BaseModel):
    code: str = Field(max_length=10)
    modifiers: list[str] = Field(default_factory=list, max_length=4)
    units: int = Field(default=1, ge=1, le=99)


class Codes(BaseModel):
    icd: list[str] = Field(max_length=12)
    cpt: list[CptLine] = Field(max_length=12)
    reason: str | None = Field(default=None, max_length=1000)


@router.post("/encounters/{enc_id}/check")
def check_codes(enc_id: int, body: Codes, db: Session = Depends(get_db), _: User = Depends(current_user)):
    """Run the coding checks on a coder's working set of codes, without saving."""
    from app.coding import changes_against, note_of
    from app.verticals.healthcare.coding_rules import coding_edits
    enc = _load(db, enc_id)
    icd, cpt, problems = validate_codes(body.icd, [l.model_dump() for l in body.cpt])
    note = note_of(enc)
    separate = bool(((enc.suggestion or {}).get("documented") or {}).get("separate_em", {}).get("documented"))
    return {"problems": problems, "edits": coding_edits(icd, cpt, note[1].text if note else "", separate),
            "changes": changes_against(enc.suggestion or {}, icd, cpt)}


@router.post("/encounters/{enc_id}/finalize")
def finalize_codes(enc_id: int, body: Codes, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    from app.coding import changes_against
    enc = _load(db, enc_id)
    if enc.status != EncStatus.REVIEW:
        raise HTTPException(409, f"The encounter is {LABELS[enc.status].lower()}, not waiting for a coder")
    icd, cpt, problems = validate_codes(body.icd, [l.model_dump() for l in body.cpt])
    if problems:
        raise HTTPException(400, "; ".join(problems))
    if changes_against(enc.suggestion or {}, icd, cpt) and not (body.reason or "").strip():
        raise HTTPException(400, "You changed the Coding Agent's codes: say why (it's kept as a test case)")
    finalize(db, enc, user, icd, cpt, body.reason)
    return detail(db, _load(db, enc_id))


@router.post("/encounters/{enc_id}/reopen")
def reopen(enc_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    enc = _load(db, enc_id)
    if enc.status != EncStatus.CODED:
        raise HTTPException(409, "Only coded encounters that aren't billed yet can be reopened")
    transition(db, enc, EncStatus.REVIEW, user.email, "Reopened for coding changes")
    return detail(db, _load(db, enc_id))


@router.post("/encounters/{enc_id}/claim")
def make_claim(enc_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("biller"))):
    enc = _load(db, enc_id)
    if enc.status != EncStatus.CODED:
        raise HTTPException(409, "Approve the codes first")
    claim = create_claim(db, enc, user)
    return {**detail(db, _load(db, enc_id)), "claim_number": claim.number}


@router.post("/encounters/{enc_id}/retry")
def retry(enc_id: int, db: Session = Depends(get_db), user: User = Depends(require_role("biller", "intake"))):
    enc = _load(db, enc_id)
    if enc.status != EncStatus.FAILED:
        raise HTTPException(409, "Only failed encounters can be retried")
    for doc in enc.documents:
        doc.kind, doc.fields = None, {}
    transition(db, enc, EncStatus.READING, user.email, "Retry requested")
    enqueue(db, "enc_read", None, key=f"enc_read:{enc.id}:retry:{len(enc.events)}", payload={"encounter_id": enc.id})
    return detail(db, _load(db, enc_id))
