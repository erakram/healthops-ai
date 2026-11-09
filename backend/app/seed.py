"""Demo users and a few weeks of claim history, so the dashboard isn't empty on first start.

History claims are built from the hist_* and hden_* sample cases without calling an LLM:
their extracted values come from the answer key, evidence boxes are found in the PDFs,
and findings come from the real rules engine. Submitted ones go through the real mock
payer, 835 writer and parser, and denials through the real triage rules (the one LLM
judgement, policy criteria, is answered from the answer key). Older denials are shown
resolved, newer ones are left open as a worklist. Everything is marked
source="demo-history" and has no agent runs, so cost and latency figures only ever
reflect real processing, and historical appeals say their letter wasn't kept.
"""

import random
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.denials import denial_groups, triage_denial
from app.agents.extraction import INJECTION_PATTERNS
from app.core import audit
from app.core.auth import hash_password
from app.core.llm import LLMResult
from app.core.workflow import DenialStatus, Status
from app.documents.pdf import locate, parse_pdf
from app.models import Appeal, Claim, ClaimEvent, Denial, Document, Finding, Remittance, Submission, User
from app.revenue import RECEIVER_ID, SETTLE_NOTES, payer_claim, target_status
from app.verticals.healthcare import payer
from app.verticals.healthcare.carc import CARCS, describe
from app.verticals.healthcare.policies import get_policy
from app.verticals.healthcare.rules import RuleFinding, risk_level, run_rules
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted
from app.verticals.healthcare.x12_835 import build_835, parse_835

# Demo accounts for the login page. Fictitious; change or disable with SEED_DEMO_DATA=false.
DEMO_PASSWORD = "healthops-demo"
DEMO_USERS = [("intake@healthops.demo", "Jordan Lee", "intake"),
              ("biller@healthops.demo", "Sam Rivera", "biller"),
              ("admin@healthops.demo", "Alex Morgan", "admin")]
BILLER = "biller@healthops.demo"

PATH_TO = {  # how far through the lifecycle a history claim got
    Status.SUBMITTED: [Status.EXTRACTING, Status.VALIDATING, "_review", Status.APPROVED, Status.SUBMITTED],
    Status.REJECTED: [Status.EXTRACTING, Status.VALIDATING, "_review", Status.REJECTED],
    Status.NEEDS_REVIEW: [Status.EXTRACTING, Status.VALIDATING, Status.NEEDS_REVIEW],
    Status.READY: [Status.EXTRACTING, Status.VALIDATING, Status.READY],
}


class AnswerKeyAssessor:
    """Stands in for the model's policy-criteria judgement, from the case's answer key."""

    def __init__(self, case: dict):
        self.quotes = (case.get("denial") or case.get("pa") or {}).get("criteria_quotes", {})

    def complete_json(self, system: str, user: str, model: str | None = None) -> LLMResult:
        import json
        payload = json.loads(user)
        documentation = " ".join(payload["documentation"].split())
        found = {cid: q for cid, q in self.quotes.items() if q in documentation}
        return LLMResult({"criteria": [{"id": c["id"], "met": c["id"] in found, "quote": found.get(c["id"], ""),
                                        "explanation": "" if c["id"] in found else "Not documented in the note."}
                                       for c in payload["policy"]["criteria"]], "summary": ""}, "answer-key", 0, 0, 0)


def seed_users(db: Session) -> None:
    if db.scalar(select(func.count(User.id))):
        return
    for email, name, role in DEMO_USERS:
        db.add(User(email=email, name=name, role=role, password_hash=hash_password(DEMO_PASSWORD)))
    db.commit()


def _event(db: Session, claim: Claim, at: datetime, actor: str, message: str, kind: str = "transition", **data):
    db.add(ClaimEvent(claim_id=claim.id, type=kind, actor=actor, message=message, data=data, at=at))


def _build_claim(db: Session, case: dict, number: str, created_at: datetime, seen: list[dict]) -> tuple[Claim, list]:
    claim = Claim(number=number, status=Status.RECEIVED, source="demo-history", created_at=created_at,
                  updated_at=created_at)
    db.add(claim)
    db.flush()
    parsed, security = {}, []
    for filename in case["files"]:
        path = f"{case['path']}/{filename}"
        pdf = parse_pdf(path)
        doc = Document(claim_id=claim.id, filename=filename, path=path, kind=FILE_KINDS.get(filename),
                       pages=pdf.pages, text=pdf.text)
        db.add(doc)
        db.flush()
        parsed[doc.kind] = (doc.id, pdf)
        if match := INJECTION_PATTERNS.search(pdf.text):
            hit = locate(pdf, match.group(0))
            security.append({"document_id": doc.id, "filename": filename, "text": match.group(0),
                             "evidence": {"document_id": doc.id, **hit} if hit else None})

    record = truth_to_extracted(case["record"], parsed)
    for kind, (doc_id, _) in parsed.items():
        db.get(Document, doc_id).fields = record.get(kind, {})
    claim.extracted = {**record, "_security": security}
    form = record.get("claim_form", {})
    claim.patient_name = (form.get("patient_name") or {}).get("value")
    claim.payer_name = (form.get("payer_name") or {}).get("value")
    claim.total_charge = (form.get("total_charge") or {}).get("value")
    claim.service_date = (form.get("service_date") or {}).get("value")

    findings = run_rules(record, seen)
    findings[:0] = [RuleFinding("PROMPT-INJECTION", "critical", f"{s['filename']} contains instructions aimed at the AI",
                                f"The document says: \"{s['text']}\". It was treated as data and ignored.",
                                [s["evidence"]] if s["evidence"] else []) for s in security]
    for f in findings:
        db.add(Finding(claim_id=claim.id, code=f.code, severity=f.severity, source=f.source,
                       title=f.title, detail=f.detail, evidence=f.evidence))
    claim.risk = risk_level(findings)
    seen.append({"number": claim.number, "member_id": (form.get("member_id") or {}).get("value"),
                 "service_date": claim.service_date,
                 "cpt_codes": [i["cpt"] for i in (form.get("line_items") or {}).get("value", [])]})
    return claim, findings


def _walk(db: Session, claim: Claim, final: Status, findings: list, rng: random.Random) -> datetime:
    """Write the claim's lifecycle events up to `final`. Returns when it got there."""
    at, previous = claim.created_at, Status.RECEIVED
    for step in PATH_TO[final]:
        if step == "_review":
            step = Status.NEEDS_REVIEW if findings else Status.READY
        at += timedelta(seconds=rng.randint(4, 30)) if step in (Status.EXTRACTING, Status.VALIDATING,
                                                                Status.NEEDS_REVIEW, Status.READY) \
            else timedelta(hours=rng.randint(1, 30))
        actor = BILLER if step in (Status.APPROVED, Status.REJECTED) else "Workflow Engine"
        _event(db, claim, at, actor, f"Moved to {step.replace('_', ' ')}",
               "decision" if actor != "Workflow Engine" else "transition", **{"from": previous, "to": step})
        previous = step
    claim.status = final
    claim.updated_at = at
    return at


def _remit(db: Session, claim: Claim, clps: list[dict], kind: str, at: datetime, trace: str, control: int) -> Remittance:
    """Payer side writes the 835; provider side parses it, as in revenue.handle_remit."""
    payload = payer_claim(claim)
    remit = payer.remittance(claim.payer_name, {"name": payload["provider_name"], "npi": payload["provider_npi"]},
                             clps, at.date(), trace)
    raw = build_835(remit, payer.profile_for(claim.payer_name).id, RECEIVER_ID, control, at)
    parsed = parse_835(raw)
    row = None
    for clp in parsed["claims"]:
        row = Remittance(claim_id=claim.id, kind=kind, payer_name=parsed["payer"]["name"], trace_number=trace, raw=raw,
                         parsed={"payer": parsed["payer"], "payee": parsed["payee"], "payment": parsed["payment"],
                                 "control_number": parsed["control_number"], "warnings": parsed["warnings"],
                                 "claim": clp},
                         paid=clp["paid"], patient_responsibility=clp["patient_responsibility"], received_at=at)
        db.add(row)
    db.flush()
    return row


def _settle(db: Session, claim: Claim, at: datetime) -> None:
    target, paid = target_status(db, claim)
    if target != claim.status:
        _event(db, claim, at, "Workflow Engine", f"Settled: payer paid ${paid:,.2f}" if target == Status.PAID
               else SETTLE_NOTES.get(target, f"Moved to {target}"), **{"from": claim.status, "to": target})
        claim.status = target
        claim.updated_at = at


def _process_payer(db: Session, claim: Claim, case: dict, submitted_at: datetime, rng: random.Random,
                   now: datetime) -> None:
    payload = payer_claim(claim)
    sub = Submission(claim_id=claim.id, idempotency_key=f"submit:{claim.id}", frequency="1", payload=payload,
                     payer_reference=f"PYR-{secrets.token_hex(4).upper()}", at=submitted_at)
    db.add(sub)
    db.flush()
    received = min(submitted_at + timedelta(days=rng.randint(3, 8), hours=rng.randint(0, 8)), now - timedelta(hours=2))
    clp = payer.adjudicate(payload, submitted_at.date())
    profile = payer.profile_for(claim.payer_name)
    remit = _remit(db, claim, [clp], "original", received, f"{profile.id}{sub.id:07d}", sub.id * 10 + 1)
    clp = remit.parsed["claim"]
    _event(db, claim, received, "Payer Gateway", f"835 received ({clp['status']}): paid ${clp['paid']:,.2f}", "step",
           remittance_id=remit.id)

    for group in denial_groups(clp):
        denial = Denial(claim_id=claim.id, remittance_id=remit.id, group_code=group["group"], reason_code=group["code"],
                        remark_codes=group["remarks"], lines=group["lines"], amount=group["amount"],
                        category="other", status=DenialStatus.OPEN, created_at=received, updated_at=received)
        db.add(denial)
        db.flush()
        _event(db, claim, received, "Payer Gateway", f"{describe(group['group'], group['code'])} "
               f"(${group['amount']:,.2f})", "step", denial_id=denial.id)
        context = {"payer_name": claim.payer_name, "service_date": claim.service_date,
                   "remit_date": received.date().isoformat(), "first_submitted_on": submitted_at.date().isoformat(),
                   "claim_cpts": [l["cpt"] for l in clp["lines"]]}
        record = {k: v for k, v in claim.extracted.items() if not k.startswith("_")}
        docs = {d.kind: (d.id, parse_pdf(d.path)) for d in db.scalars(select(Document).where(Document.claim_id == claim.id))}
        result = triage_denial(group, record, docs, context, AnswerKeyAssessor(case))
        denial.category, denial.recommended_action, denial.deadline = result.category, result.action, result.deadline
        denial.win_probability, denial.expected_recovery = result.win_probability, result.expected_recovery
        denial.policy_id, denial.triage = result.policy_id, result.as_json()
        _event(db, claim, received + timedelta(minutes=1), "Denial Triage Agent",
               f"Recommendation: {result.action.replace('_', ' ')}", "step", denial_id=denial.id)
        if (now - received).days >= 6:
            _resolve(db, claim, case, denial, result, received, rng, now)
    _settle(db, claim, received + timedelta(minutes=2))


def _resolve(db, claim, case, denial, result, received, rng, now) -> None:
    """What the billing team did with an older denial."""
    done = min(received + timedelta(days=rng.randint(1, 3)), now - timedelta(hours=1))
    if result.action == "bill_patient":
        denial.status, denial.action_taken = DenialStatus.PATIENT_BILLED, "bill_patient"
        db.flush()
        from app.billing import bill_patient
        bill_patient(db, denial, BILLER, today=done.date())
    elif result.action == "write_off":
        denial.status, denial.action_taken = DenialStatus.WRITTEN_OFF, "write_off"
    elif result.action == "appeal":
        decided = min(done + timedelta(days=rng.randint(2, 5)), now - timedelta(minutes=30))
        record = {k: v for k, v in claim.extracted.items() if not k.startswith("_")}
        citations = [{"source": "clinical_note", "quote": q, "verified": True}
                     for q in (case.get("denial") or {}).get("criteria_quotes", {}).values()]
        auth = ((record.get("prior_authorization") or {}).get("auth_number") or {}).get("value")
        if auth:
            citations.append({"source": "prior_authorization", "quote": f"Authorization number: {auth}", "verified": True})
        decision = payer.review_appeal(denial.category, [l["cpt"] for l in denial.lines], citations, record,
                                       claim.service_date, get_policy(denial.policy_id))
        appeal = Appeal(denial_id=denial.id, status="overturned" if decision.overturned else "upheld",
                        draft={"historical": True}, letter={}, sent_by=BILLER, sent_at=done, decided_at=decided,
                        outcome_reason=decision.reason, created_at=done)
        db.add(appeal)
        denial.action_taken = "appeal"
        if decision.overturned:
            clp = payer.appeal_payment(denial.remittance.parsed["claim"], [l["cpt"] for l in denial.lines],
                                       claim.payer_name)
            remit = _remit(db, claim, [clp], "appeal", decided, f"{payer.profile_for(claim.payer_name).id}A{denial.id:06d}",
                           denial.id * 10 + 7)
            denial.status, denial.recovered = DenialStatus.OVERTURNED, remit.paid
        else:
            denial.status = DenialStatus.UPHELD
        done = decided
    elif result.action == "corrected_claim" and result.fix:
        fixed = payer_claim(claim, "7", {result.fix["cpt"]: [result.fix["add_modifier"]]})
        sub = Submission(claim_id=claim.id, idempotency_key=f"submit:{claim.id}:7:{denial.id}", frequency="7",
                         payload=fixed, payer_reference=f"PYR-{secrets.token_hex(4).upper()}", at=done)
        db.add(sub)
        db.flush()
        paid_at = min(done + timedelta(days=rng.randint(2, 5)), now - timedelta(minutes=30))
        new = payer.adjudicate(fixed, done.date())
        _remit(db, claim, [payer.reversal_of(denial.remittance.parsed["claim"]), new], "corrected", paid_at,
               f"{payer.profile_for(claim.payer_name).id}{sub.id:07d}", sub.id * 10 + 1)
        cpts = {l["cpt"] for l in denial.lines}
        denial.status, denial.action_taken = DenialStatus.PAID_ON_RESUBMISSION, "corrected_claim"
        denial.recovered = round(sum(l["paid"] for l in new["lines"] if l["cpt"] in cpts), 2)
        done = paid_at
    else:
        return
    denial.closed_at = done if denial.status not in (DenialStatus.UPHELD,) else None
    denial.updated_at = done
    _event(db, claim, done, BILLER if denial.status in (DenialStatus.PATIENT_BILLED, DenialStatus.WRITTEN_OFF)
           else "Payer Gateway", f"{denial.group_code}-{denial.reason_code}: "
           f"{denial.status.replace('_', ' ')}" + (f", recovered ${denial.recovered:,.2f}" if denial.recovered else ""),
           "step", denial_id=denial.id)


def seed_authorizations(db: Session, now: datetime, rng: random.Random) -> int:
    """Prior authorization history from the hpa_* cases: decided ones through the real payer
    review and FHIR exchange, and one request still waiting for the physician's addendum."""
    from app.agents.authorization import assess_documentation, check_requirement
    from app.authorizations import AuthStatus, documents_of
    from app.config import settings
    from app.models import Authorization, AuthEvent
    from app.verticals.healthcare import fhir_pas
    from app.verticals.healthcare.letters import determination_letter

    cases = list_cases("hpa_")
    left_open = False
    for index, case in enumerate(cases):
        created = now - timedelta(days=2 + (len(cases) - index) * 18 / len(cases), hours=rng.randint(0, 8))
        auth = Authorization(number=f"PAR-{39000 + index}", status=AuthStatus.RECEIVED, source="demo-history",
                             case_id=case["case_id"], created_at=created, updated_at=created)
        db.add(auth)
        db.flush()
        parsed = {}
        for filename in case["files"]:
            pdf = parse_pdf(f"{case['path']}/{filename}")
            doc = Document(authorization_id=auth.id, filename=filename, path=f"{case['path']}/{filename}",
                           kind=FILE_KINDS.get(filename), pages=pdf.pages, text=pdf.text)
            db.add(doc)
            db.flush()
            parsed[doc.kind] = (doc.id, pdf)
        record = truth_to_extracted(case["record"], parsed)
        for kind, (doc_id, _) in parsed.items():
            db.get(Document, doc_id).fields = record.get(kind, {})
        from app.authorizations import _apply_record
        _apply_record(auth, record)
        db.flush()
        db.refresh(auth)

        at = created + timedelta(seconds=rng.randint(20, 60))
        steps = [(created, "transition", "intake@healthops.demo", "Received 3 document(s)")]
        requirement = check_requirement(record, created.date())
        auth.requirement = requirement
        key, scenario = case["pa"], case["scenario"]

        def finish(status, message, when):
            auth.status = status
            auth.updated_at = when
            steps.append((when, "transition", "Workflow Engine", message))

        if not requirement["eligible"]:
            finish(AuthStatus.INELIGIBLE, "Coverage isn't active on the scheduled date", at)
        elif not requirement["required"]:
            auth.assessment = {"action": "none", "reasons": requirement["reasons"], "criteria": [], "missing": []}
            finish(AuthStatus.NOT_REQUIRED, "No authorization needed: schedule the service", at)
        else:
            from app.verticals.healthcare.policies import get_policy
            policy = get_policy(requirement["policy_id"])
            notes = {k: v for k, v in documents_of(auth).items() if k.startswith("clinical_note")}
            assessment = assess_documentation(AnswerKeyAssessor(case), policy, auth.services, notes)
            auth.assessment = {**assessment.as_json(), "policy_id": policy["id"]}
            if scenario == "missing_documentation" and not left_open:
                left_open = True  # waiting for the physician: the demo can add the sample addendum
                finish(AuthStatus.NEEDS_DOCUMENTATION, "Ask the physician for documentation", at)
            else:
                if scenario == "missing_documentation":  # the addendum came in before it was sent
                    extra = f"{case['path']}/extra/addendum_note.pdf"
                    pdf = parse_pdf(extra)
                    db.add(Document(authorization_id=auth.id, filename="addendum_note.pdf", path=extra,
                                    kind="clinical_note", pages=pdf.pages, text=pdf.text))
                    db.flush()
                    db.refresh(auth)
                    notes = {k: v for k, v in documents_of(auth).items() if k.startswith("clinical_note")}
                    assessment = assess_documentation(AnswerKeyAssessor(case), policy, auth.services, notes)
                    auth.assessment = {**assessment.as_json(), "policy_id": policy["id"]}
                    steps.append((at, "decision", "intake@healthops.demo", "Added documentation: addendum_note.pdf"))
                auth.draft = {"historical": True}
                quotes = [{"source": c["source"], "quote": c["quote"]} for c in assessment.criteria if c["met"]]
                attached = [{"title": "Clinical note" if k == "clinical_note" else "Clinical note (addendum)",
                             "text": pdf.text} for k, (_, pdf) in notes.items()]
                attempts = 2 if assessment.action == "do_not_submit" else 1
                decided = at
                for attempt in range(1, attempts + 1):
                    sent = decided + timedelta(hours=rng.randint(1, 20))
                    order = {"request_number": auth.number, "attempt": attempt, "order_number": auth.number,
                             "urgency": auth.urgency, "scheduled_date": auth.scheduled_date,
                             "patient": {"name": auth.patient_name, "dob": None, "member_id": auth.member_id},
                             "payer": {"name": auth.payer_name, "id": payer.profile_for(auth.payer_name).id},
                             "provider": {"name": "Provider", "npi": ""}, "services": auth.services,
                             "diagnosis_codes": []}
                    auth.fhir_request = fhir_pas.build_request(order, quotes, attached, "Historical request", sent)
                    request = fhir_pas.read_request(auth.fhir_request)
                    decided = min(sent + timedelta(hours=rng.randint(4, 30 if auth.urgency == "urgent" else 90)),
                                  now - timedelta(minutes=30))
                    review = payer.review_authorization(request, auth.payer_name, attempt, sent.date())
                    auth.fhir_response = fhir_pas.build_response(request, review, decided)
                    decision = fhir_pas.read_response(auth.fhir_response)
                    steps.append((sent, "transition", "intake@healthops.demo", f"Submitted (attempt {attempt})"))
                auth.attempts, auth.submitted_at, auth.decided_at = attempts, sent, decided
                auth.due_at = sent + (timedelta(hours=72) if auth.urgency == "urgent" else timedelta(days=7))
                auth.decision = {**decision, "satisfied": review["satisfied"], "missing": review["missing"]}
                if decision["decision"] == "approved":
                    auth.auth_number = decision["auth_number"]
                    folder = settings.storage_dir / f"auth_{auth.id}"
                    folder.mkdir(parents=True, exist_ok=True)
                    path = folder / "authorization_determination.pdf"
                    determination_letter(str(path), payer=payer.profile_for(auth.payer_name).name,
                                         auth_number=decision["auth_number"], member_id=auth.member_id or "",
                                         patient=auth.patient_name or "", valid_from=decision["valid_from"],
                                         valid_to=decision["valid_to"], services=auth.services, request_number=auth.number)
                    pdf = parse_pdf(str(path))
                    db.add(Document(authorization_id=auth.id, filename=path.name, path=str(path),
                                    kind="prior_authorization", pages=pdf.pages, text=pdf.text))
                finish({"approved": AuthStatus.APPROVED, "denied": AuthStatus.DENIED, "pended": AuthStatus.PENDED}
                       [decision["decision"]], decision["disposition"], decided)
        for when, kind, actor, message in steps:
            db.add(AuthEvent(authorization_id=auth.id, type=kind, actor=actor, message=message, at=when))
    db.commit()
    return len(cases)


def seed_encounters(db: Session, now: datetime, rng: random.Random) -> int:
    """Coding history from the hcod_* cases: the real code search, E/M rules and checks, with the
    answer key standing in for the model. Most are coded; the newest two wait for a coder."""
    from app.agents.coding import code_encounter
    from app.coding import EncStatus
    from app.models import Encounter, EncounterEvent
    from app.verticals.healthcare.codesets import icd10
    from app.verticals.healthcare.samples import coding_reply

    cases = list_cases("hcod_")
    every = list_cases("")

    class AnswerKeyCoder:
        def complete_json(self, system, user, model=None):
            return LLMResult(coding_reply(every, system, user), "answer-key", 0, 0, 0)

    for index, case in enumerate(cases):
        created = now - timedelta(days=1 + (len(cases) - index) * 1.5, hours=rng.randint(0, 6))
        enc = Encounter(number=f"ENC-{49000 + index}", status=EncStatus.RECEIVED, source="demo-history",
                        case_id=case["case_id"], created_at=created, updated_at=created)
        db.add(enc)
        db.flush()
        parsed = {}
        for filename in case["files"]:
            pdf = parse_pdf(f"{case['path']}/{filename}")
            doc = Document(encounter_id=enc.id, filename=filename, path=f"{case['path']}/{filename}",
                           kind=FILE_KINDS.get(filename), pages=pdf.pages, text=pdf.text)
            db.add(doc)
            db.flush()
            parsed[doc.kind] = (doc.id, pdf)
        record = truth_to_extracted(case["record"], parsed)
        for kind, (doc_id, _) in parsed.items():
            db.get(Document, doc_id).fields = record.get(kind, {})
        enc.extracted = record
        value = lambda doc, name: ((record.get(doc) or {}).get(name) or {}).get("value")  # noqa: E731
        enc.patient_name, enc.payer_name = value("clinical_note", "patient_name"), value("insurance_card", "payer_name")
        enc.provider_name, enc.service_date = value("clinical_note", "practice_name"), value("clinical_note", "service_date")
        result = code_encounter(AnswerKeyCoder(), parsed["clinical_note"])
        enc.suggestion = result.as_json()
        steps = [(created, "transition", "biller@healthops.demo", "Received 2 document(s)"),
                 (created + timedelta(seconds=20), "step", "Coding Agent",
                  "Suggested " + ", ".join([d["code"] for d in result.diagnoses] + [p["code"] for p in result.procedures]))]
        if index >= len(cases) - 2:
            enc.status = EncStatus.REVIEW
        else:
            icd = [d["code"] for d in result.diagnoses]
            cpt = [{"code": p["code"], "modifiers": p["modifiers"], "units": p["units"], "description": p["description"]}
                   for p in result.procedures]
            changes, reason = [], None
            if index == 1:  # one coder added a secondary diagnosis
                extra = "Z79.84" if "Z79.84" in icd10() else None
                if extra and extra not in icd:
                    icd.append(extra)
                    changes, reason = [{"kind": "icd", "change": "added", "code": extra}], "Long-term oral hypoglycemic use documented"
            enc.final = {"icd": [{"code": c, "description": icd10()[c]} for c in icd], "cpt": cpt, "edits": [],
                         "changes": changes, "reason": reason, "agreed": not changes}
            enc.status, enc.coded_by = EncStatus.CODED, "biller@healthops.demo"
            enc.coded_at = created + timedelta(hours=rng.randint(1, 20))
            steps.append((enc.coded_at, "decision", "biller@healthops.demo",
                          "Sam Rivera approved the codes" + (f" with 1 change: {reason}" if changes else ", unchanged")))
        enc.updated_at = steps[-1][0]
        for when, kind, actor, message in steps:
            db.add(EncounterEvent(encounter_id=enc.id, type=kind, actor=actor, message=message, at=when))
    db.commit()
    return len(cases)


def _seed_payments(db: Session, now: datetime) -> None:
    """Some patients paid, one is on a payment plan."""
    from app.billing import record_payment, set_up_plan
    from app.models import Statement
    biller = db.scalar(select(User).where(User.email == BILLER))
    statements = db.scalars(select(Statement).order_by(Statement.id)).all()
    for index, s in enumerate(statements):
        if index == 0:
            record_payment(db, s, s.total, "card", "AUTH-58213", biller, today=now.date())
        elif index == 1:
            set_up_plan(db, s, 3, biller, today=now.date())
            record_payment(db, s, s.plan["schedule"][0]["amount"], "check", "CHK-1042", biller, today=now.date())


def seed_history(db: Session) -> int:
    if db.scalar(select(func.count(Claim.id))):
        return 0
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    seen: list[dict] = []
    created = 0

    cases = list_cases("hist_")
    for index, case in enumerate(cases):
        age_days = (len(cases) - index) * 21 / max(len(cases), 1)
        created_at = now - timedelta(days=age_days, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
        claim, findings = _build_claim(db, case, f"CLM-{20000 + index}", created_at, seen)
        # Older claims are finished; the newest ones are still in the queue.
        if age_days > 3:
            final = Status.REJECTED if claim.risk == "high" else Status.SUBMITTED
        else:
            final = Status.NEEDS_REVIEW if any(f.severity != "low" for f in findings) else Status.READY
        submitted_at = _walk(db, claim, final, findings, rng)
        if final == Status.SUBMITTED:
            _process_payer(db, claim, case, submitted_at, rng, now)
        created += 1

    # Claims the payer denied: older ones already worked, recent ones waiting in the worklist.
    denial_cases = list_cases("hden_")
    for index, case in enumerate(denial_cases):
        age_days = 4 + (len(denial_cases) - index) * 20 / max(len(denial_cases), 1)
        created_at = now - timedelta(days=age_days, hours=rng.randint(0, 9))
        claim, findings = _build_claim(db, case, f"CLM-{21000 + index}", created_at, seen)
        submitted_at = _walk(db, claim, Status.SUBMITTED, findings, rng)
        _process_payer(db, claim, case, submitted_at, rng, now)
        created += 1
    # Claims the payer never answered: the accounts receivable worklist.
    for index, case in enumerate(list_cases("har_")):
        from app.verticals.healthcare.schema import parse_date
        dos = datetime.combine(parse_date(case["record"]["claim_form"]["service_date"]), datetime.min.time(), timezone.utc)
        created_at = dos + timedelta(days=rng.randint(1, 3), hours=rng.randint(8, 16))
        claim, findings = _build_claim(db, case, f"CLM-{22000 + index}", created_at, seen)
        sent = _walk(db, claim, Status.SUBMITTED, findings, rng)
        db.add(Submission(claim_id=claim.id, idempotency_key=f"submit:{claim.id}", frequency="1",
                          payload=payer_claim(claim), payer_reference=f"PYR-{secrets.token_hex(4).upper()}", at=sent))
        created += 1
    db.commit()
    _seed_payments(db, now)
    authorizations = seed_authorizations(db, now, rng)
    encounters = seed_encounters(db, now, rng)
    if created:
        audit.record(db, "system", "demo.seeded", "system", "history",
                     {"claims": created, "remittances": db.scalar(select(func.count(Remittance.id))),
                      "denials": db.scalar(select(func.count(Denial.id))), "authorizations": authorizations,
                      "encounters": encounters})
    return created


from app.seed_opd import seed_opd  # noqa: E402,F401  (front office demo data, seeded from main)
