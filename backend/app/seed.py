"""Demo users and a few weeks of claim history, so the dashboard isn't empty on first start.

History claims are built from the hist_* sample cases without calling an LLM: their
extracted values come from the answer key, evidence boxes are found in the PDFs, and
findings come from the real rules engine. They're marked source="demo-history" and
have no agent runs, so cost and latency figures only ever reflect real processing.
"""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agents.extraction import INJECTION_PATTERNS
from app.core import audit
from app.core.auth import hash_password
from app.core.workflow import Status
from app.documents.pdf import locate, parse_pdf
from app.models import Claim, ClaimEvent, Document, Finding, User
from app.verticals.healthcare.rules import RuleFinding, risk_level, run_rules
from app.verticals.healthcare.samples import FILE_KINDS, list_cases, truth_to_extracted

# Demo accounts for the login page. Fictitious; change or disable with SEED_DEMO_DATA=false.
DEMO_PASSWORD = "healthops-demo"
DEMO_USERS = [("intake@healthops.demo", "Jordan Lee", "intake"),
              ("biller@healthops.demo", "Sam Rivera", "biller"),
              ("admin@healthops.demo", "Alex Morgan", "admin")]

PATH_TO = {  # how far through the lifecycle a history claim got
    Status.SUBMITTED: [Status.EXTRACTING, Status.VALIDATING, "_review", Status.APPROVED, Status.SUBMITTED],
    Status.REJECTED: [Status.EXTRACTING, Status.VALIDATING, "_review", Status.REJECTED],
    Status.NEEDS_REVIEW: [Status.EXTRACTING, Status.VALIDATING, Status.NEEDS_REVIEW],
    Status.READY: [Status.EXTRACTING, Status.VALIDATING, Status.READY],
}


def seed_users(db: Session) -> None:
    if db.scalar(select(func.count(User.id))):
        return
    for email, name, role in DEMO_USERS:
        db.add(User(email=email, name=name, role=role, password_hash=hash_password(DEMO_PASSWORD)))
    db.commit()


def seed_history(db: Session) -> int:
    if db.scalar(select(func.count(Claim.id))):
        return 0
    cases = list_cases("hist_")
    rng = random.Random(7)
    now = datetime.now(timezone.utc)
    seen: list[dict] = []
    created = 0
    for index, case in enumerate(cases):
        age_days = (len(cases) - index) * 21 / max(len(cases), 1)
        created_at = now - timedelta(days=age_days, hours=rng.randint(0, 9), minutes=rng.randint(0, 59))
        claim = Claim(number=f"CLM-{20000 + index}", status=Status.RECEIVED, source="demo-history",
                      created_at=created_at, updated_at=created_at)
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

        # Older claims are finished; the newest ones are still in the queue.
        serious = claim.risk == "high"
        if age_days > 3:
            final = Status.REJECTED if serious else Status.SUBMITTED
        else:
            final = Status.NEEDS_REVIEW if findings and any(f.severity != "low" for f in findings) else Status.READY
        at = created_at
        previous = Status.RECEIVED
        for step in PATH_TO[final]:
            if step == "_review":
                step = Status.NEEDS_REVIEW if findings else Status.READY
            at += timedelta(seconds=rng.randint(4, 30)) if step in (Status.EXTRACTING, Status.VALIDATING, Status.NEEDS_REVIEW, Status.READY) \
                else timedelta(hours=rng.randint(1, 30))
            actor = "biller@healthops.demo" if step in (Status.APPROVED, Status.REJECTED) else "Workflow Engine"
            db.add(ClaimEvent(claim_id=claim.id, type="decision" if actor != "Workflow Engine" else "transition",
                              actor=actor, message=f"Moved to {step.replace('_', ' ')}",
                              data={"from": previous, "to": step}, at=at))
            previous = step
        claim.status = final
        claim.updated_at = at
        created += 1
    db.commit()
    if created:
        audit.record(db, "system", "demo.seeded", "system", "history", {"claims": created})
    return created
