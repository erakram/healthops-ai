"""Medical coding workflow: one handler per job kind, like the other workflows.

    received -> reading -> coding -> review          (Document Agent, then the Coding Agent)
    review -> coded                                 (a coder approves the codes, giving a reason for every change)
    coded -> billed                                 (a claim is generated and enters the claim pipeline)
    reading/coding -> failed -> reading             (retry)
"""

import shutil
import time
from datetime import date, datetime, timezone
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.coding import code_encounter
from app.agents.extraction import extract_document
from app.config import settings
from app.core import audit, events
from app.core.llm import LLM, LLMError, LLMResult
from app.core.workflow import InvalidTransition, enqueue
from app.documents.pdf import parse_pdf
from app.models import AgentRun, Authorization, Claim, ClaimEvent, Document, Encounter, EncounterEvent, User  # noqa: F401
from app.verticals.healthcare.codesets import CPT_CODES, dotted, icd10
from app.verticals.healthcare.coding_rules import coding_edits
from app.verticals.healthcare.letters import claim_form, itemized_invoice
from app.verticals.healthcare.schema import DOCUMENT_TYPES, normalize_id

DOCUMENT_AGENT = "Document Agent"
CODING_AGENT = "Coding Agent"
SYSTEM = "Workflow Engine"


class EncStatus(StrEnum):
    RECEIVED = "received"
    READING = "reading"
    CODING = "coding"
    REVIEW = "review"
    CODED = "coded"
    BILLED = "billed"
    FAILED = "failed"


E = EncStatus
TRANSITIONS = {
    E.RECEIVED: {E.READING, E.FAILED}, E.READING: {E.CODING, E.FAILED}, E.CODING: {E.REVIEW, E.FAILED},
    E.REVIEW: {E.CODED, E.CODING}, E.CODED: {E.BILLED, E.REVIEW}, E.BILLED: set(), E.FAILED: {E.READING},
}


def now() -> datetime:
    return datetime.now(timezone.utc)


def transition(db: Session, enc: Encounter, to: EncStatus, actor: str, note: str | None = None) -> None:
    current = EncStatus(enc.status)
    if to not in TRANSITIONS[current]:
        raise InvalidTransition(f"{enc.number} can't go from {current} to {to}")
    enc.status = to
    db.add(EncounterEvent(encounter_id=enc.id, type="transition", actor=actor,
                          message=note or f"Moved to {to.replace('_', ' ')}", data={"from": current, "to": to}))
    audit.record(db, actor, "encounter.transition", "encounter", enc.number, {"from": current, "to": to, "note": note},
                 commit=False)
    db.commit()
    events.publish("encounter.updated", encounter_id=enc.id, status=to)


def log_step(db: Session, enc: Encounter, actor: str, message: str, kind: str = "step", **data) -> None:
    db.add(EncounterEvent(encounter_id=enc.id, type=kind, actor=actor, message=message, data=data))
    db.commit()
    events.publish("encounter.step", encounter_id=enc.id, agent=actor, message=message)


def _record_run(db: Session, enc: Encounter, agent: str, result: LLMResult | None) -> None:
    if result:
        db.add(AgentRun(encounter_id=enc.id, agent=agent, model=result.model, input_tokens=result.input_tokens,
                        output_tokens=result.output_tokens, latency_ms=result.latency_ms))


def note_of(enc: Encounter):
    doc = next((d for d in enc.documents if d.kind == "clinical_note"), None)
    return (doc.id, parse_pdf(doc.path)) if doc else None


def handle_enc_read(db: Session, _claim, llm: LLM | None, payload: dict) -> None:
    enc = db.get(Encounter, payload["encounter_id"])
    if llm is None:
        raise LLMError(f"No LLM configured: set {settings.llm_key_name} to read encounters")
    if enc.status != EncStatus.READING:
        transition(db, enc, EncStatus.READING, SYSTEM, "Document Agent started")
    record: dict = {}
    for doc in enc.documents:
        if doc.kind is None:
            log_step(db, enc, DOCUMENT_AGENT, f"Reading {doc.filename}", document_id=doc.id)
            parsed = parse_pdf(doc.path)
            if parsed.ocr_pages or parsed.needs_ocr:
                log_step(db, enc, DOCUMENT_AGENT, f"{doc.filename}: scanned page(s) "
                         + (f"{parsed.ocr_pages} read with on-device OCR" if parsed.ocr_pages else
                            f"{parsed.needs_ocr} need a person (no OCR on this server)"), document_id=doc.id)
            result = extract_document(llm, parsed, doc.id, model=settings.extraction_model)
            _record_run(db, enc, DOCUMENT_AGENT, result.llm)
            doc.pages, doc.text, doc.kind, doc.fields = parsed.pages, parsed.text, result.document_type, result.fields
            db.commit()
            label = DOCUMENT_TYPES.get(result.document_type, {}).get("label", "Unrecognised document")
            log_step(db, enc, DOCUMENT_AGENT, f"{doc.filename} is a {label.lower()}: {len(result.fields)} fields read",
                     document_id=doc.id)
        if doc.kind in DOCUMENT_TYPES and doc.kind not in record:
            record[doc.kind] = doc.fields or {}
    enc.extracted = record
    note, card = record.get("clinical_note") or {}, record.get("insurance_card") or {}
    value = lambda doc, name: (doc.get(name) or {}).get("value")  # noqa: E731
    enc.patient_name = value(note, "patient_name") or value(card, "member_name")
    enc.payer_name = value(card, "payer_name")
    enc.provider_name = value(note, "practice_name")
    enc.service_date = value(note, "service_date")
    db.commit()
    transition(db, enc, EncStatus.CODING, SYSTEM, "Documents read, coding the encounter")
    enqueue(db, "enc_code", None, key=f"enc_code:{enc.id}:{time.time_ns()}", payload={"encounter_id": enc.id})


def handle_enc_code(db: Session, _claim, llm: LLM | None, payload: dict) -> None:
    enc = db.get(Encounter, payload["encounter_id"])
    if enc.status != EncStatus.CODING:
        return
    note = note_of(enc)
    if note is None:
        enc.suggestion = {"diagnoses": [], "procedures": [], "em": None, "documented": {}, "stats": {},
                          "edits": coding_edits([], [], "", False)}
        db.commit()
        transition(db, enc, EncStatus.REVIEW, SYSTEM, "No visit note in the packet: code by hand")
        return
    if llm is None:
        raise LLMError(f"No LLM configured: set {settings.llm_key_name} to code encounters")
    log_step(db, enc, CODING_AGENT, "Reading the note for diagnoses, procedures and visit complexity")
    result = code_encounter(llm, note, model=settings.review_model)
    for r in result.llm:
        _record_run(db, enc, CODING_AGENT, r)
    enc.suggestion = result.as_json()
    db.commit()
    log_step(db, enc, CODING_AGENT, f"Searched 74,719 ICD-10-CM codes for {len(result.diagnoses)} documented "
             f"diagnosis(es); codes picked from the search results only" + (f" ({result.stats['rejected_choices']} off-list answer(s) rejected)"
                                                    if result.stats["rejected_choices"] else ""))
    if result.em:
        log_step(db, enc, CODING_AGENT, f"Visit level {result.em['code']}: {result.em['explanation']}")
    codes = ", ".join([d["code"] for d in result.diagnoses] +
                      [p["code"] + ("-" + "-".join(p["modifiers"]) if p["modifiers"] else "") for p in result.procedures])
    log_step(db, enc, CODING_AGENT, f"Suggested {codes or 'no codes'}; {len(result.edits)} coding check(s) raised")
    transition(db, enc, EncStatus.REVIEW, SYSTEM, "Waiting for a coder")


def validate_codes(icd: list[str], cpt: list[dict]) -> tuple[list[str], list[dict], list[str]]:
    """Normalize what a coder submitted; return problems that block finalizing."""
    problems = []
    icd_out = []
    for code in icd:
        c = dotted(code)
        if c not in icd10():
            problems.append(f"{c} isn't a billable ICD-10-CM code")
        elif c not in icd_out:
            icd_out.append(c)
    cpt_out = []
    for line in cpt:
        code = str(line.get("code") or "").strip()
        if code not in CPT_CODES:
            problems.append(f"CPT {code} isn't in the code list")
            continue
        mods = [m for m in (str(x).strip().upper() for x in line.get("modifiers") or []) if m]
        units = int(line.get("units") or 1)
        cpt_out.append({"code": code, "modifiers": mods, "units": max(units, 1), "description": CPT_CODES[code][0]})
    if not icd_out:
        problems.append("At least one diagnosis code is needed")
    if not cpt_out:
        problems.append("At least one procedure or visit code is needed")
    return icd_out, cpt_out, problems


def changes_against(suggestion: dict, icd: list[str], cpt: list[dict]) -> list[dict]:
    """What the coder changed compared with the agent: every change needs a reason."""
    suggested_icd = [d["code"] for d in suggestion.get("diagnoses", [])]
    suggested_cpt = {(p["code"], tuple(p.get("modifiers") or []), p.get("units") or 1) for p in suggestion.get("procedures", [])}
    final_cpt = {(p["code"], tuple(p["modifiers"]), p["units"]) for p in cpt}
    changes = [{"kind": "icd", "change": "added", "code": c} for c in icd if c not in suggested_icd]
    changes += [{"kind": "icd", "change": "removed", "code": c} for c in suggested_icd if c not in icd]
    changes += [{"kind": "cpt", "change": "added", "code": "-".join([c, *m]) + (f" x{u}" if u > 1 else "")}
                for c, m, u in final_cpt - suggested_cpt]
    changes += [{"kind": "cpt", "change": "removed", "code": "-".join([c, *m]) + (f" x{u}" if u > 1 else "")}
                for c, m, u in suggested_cpt - final_cpt]
    if not changes and suggested_icd != icd:
        changes.append({"kind": "icd", "change": "reordered", "code": icd[0]})
    return changes


def finalize(db: Session, enc: Encounter, user: User, icd: list[str], cpt: list[dict], reason: str | None) -> list[dict]:
    note = note_of(enc)
    separate = bool(((enc.suggestion or {}).get("documented") or {}).get("separate_em", {}).get("documented"))
    edits = coding_edits(icd, cpt, note[1].text if note else "", separate)
    changes = changes_against(enc.suggestion or {}, icd, cpt)
    enc.final = {"icd": [{"code": c, "description": icd10()[c]} for c in icd], "cpt": cpt, "edits": edits,
                 "changes": changes, "reason": reason, "agreed": not changes}
    enc.coded_by, enc.coded_at = user.email, now()
    db.add(EncounterEvent(encounter_id=enc.id, type="decision", actor=user.email,
                          message=f"{user.name} approved the codes" + (f" with {len(changes)} change(s): {reason}" if changes
                                                                         else ", unchanged from the Coding Agent"),
                          data={"changes": changes}))
    audit.record(db, user.email, "encounter.coded", "encounter", enc.number,
                 {"icd": icd, "cpt": [f"{p['code']}{'-' + '-'.join(p['modifiers']) if p['modifiers'] else ''} x{p['units']}"
                                      for p in cpt], "changes": changes, "reason": reason}, commit=False)
    transition(db, enc, EncStatus.CODED, user.email, "Codes approved")
    return edits


def _approved_authorization(db: Session, enc: Encounter, member_id: str, cpts: list[str]) -> Authorization | None:
    for auth in db.scalars(select(Authorization).where(Authorization.status == "approved").order_by(Authorization.id.desc())):
        if normalize_id(auth.member_id) == normalize_id(member_id) and {s["cpt"] for s in auth.services} & set(cpts):
            return auth
    return None


def create_claim(db: Session, enc: Encounter, user: User) -> Claim:
    """Generate the claim packet from the approved codes and send it through the claim pipeline."""
    from app.api.claims import _new_claim, _start_pipeline

    note, card = enc.extracted.get("clinical_note") or {}, enc.extracted.get("insurance_card") or {}
    value = lambda doc, name: (doc.get(name) or {}).get("value")  # noqa: E731
    lines = [{"cpt": p["code"], "modifiers": p["modifiers"], "description": CPT_CODES[p["code"]][0], "units": p["units"],
              "charge": round(CPT_CODES[p["code"]][1] * p["units"], 2)} for p in enc.final["cpt"]]
    us = lambda iso: date.fromisoformat(iso).strftime("%m/%d/%Y") if iso else ""  # noqa: E731
    member = value(card, "member_id") or ""
    auth = _approved_authorization(db, enc, member, [l["cpt"] for l in lines])

    claim = _new_claim(db, user, "coding")
    folder = settings.storage_dir / f"claim_{claim.id}"
    folder.mkdir(parents=True, exist_ok=True)
    data = {"claim_number": enc.number, "payer_name": value(card, "payer_name") or enc.payer_name or "",
            "patient_name": value(note, "patient_name") or enc.patient_name or "", "patient_dob": us(value(note, "patient_dob")),
            "member_id": member, "provider_name": value(note, "practice_name") or "Provider",
            "provider_npi": value(note, "practice_npi") or "", "service_date": us(enc.service_date),
            "diagnosis_codes": [c["code"] for c in enc.final["icd"]], "prior_auth_number": auth.auth_number if auth else None,
            "line_items": lines, "invoice_number": f"INV-{enc.id:05d}"}
    claim_form(str(folder / "claim_form.pdf"), data)
    itemized_invoice(str(folder / "itemized_invoice.pdf"), data)
    files = ["claim_form.pdf", "itemized_invoice.pdf"]
    for doc in enc.documents:
        if doc.kind in ("insurance_card", "clinical_note"):
            shutil.copy(doc.path, folder / f"{doc.kind}.pdf")
            files.append(f"{doc.kind}.pdf")
    if auth:
        letter = next((d for d in auth.documents if d.kind == "prior_authorization"), None)
        if letter:
            shutil.copy(letter.path, folder / "prior_authorization.pdf")
            files.append("prior_authorization.pdf")
    for name in files:
        db.add(Document(claim_id=claim.id, filename=name, path=str(folder / name)))
    enc.claim_id = claim.id
    db.flush()
    db.add(ClaimEvent(claim_id=claim.id, type="step", actor=SYSTEM,
                      message=f"Generated from coded encounter {enc.number}"
                              + (f", with prior authorization {auth.auth_number} ({auth.number})" if auth else ""),
                      data={"encounter_id": enc.id}))
    _start_pipeline(db, claim, user, files)
    log_step(db, enc, user.email, f"Claim {claim.number} generated ({len(lines)} line(s), "
                                  f"${sum(l['charge'] for l in lines):,.2f})"
             + (f" with authorization {auth.auth_number} attached" if auth else ""), kind="decision", claim_id=claim.id)
    transition(db, enc, EncStatus.BILLED, user.email, f"Sent to the claim pipeline as {claim.number}")
    return claim


def give_up(db: Session, payload: dict, message: str) -> None:
    enc = db.get(Encounter, payload.get("encounter_id"))
    if enc and EncStatus.FAILED in TRANSITIONS[EncStatus(enc.status)]:
        transition(db, enc, EncStatus.FAILED, SYSTEM, f"Processing stopped: {message[:300]}")


HANDLERS = {"enc_read": handle_enc_read, "enc_code": handle_enc_code}
