"""Dashboard numbers, the audit log and the live event stream."""

import asyncio
import json
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.serializers import iso
from app.core import audit, events
from app.core.auth import current_user, require_role
from app.core.workflow import DENIAL_OPEN, Status
from app.db import get_db
from app.models import AgentRun, Appeal, AuditLog, Claim, ClaimEvent, Denial, Finding, Remittance, User

router = APIRouter(prefix="/api", tags=["insights"])

IN_PROGRESS = {Status.RECEIVED, Status.EXTRACTING, Status.VALIDATING}
SENT = {Status.SUBMITTED, Status.PAID, Status.DENIED, Status.APPEALED, Status.CLOSED}


def _day(value: datetime) -> str:
    return (value if value.tzinfo else value.replace(tzinfo=timezone.utc)).date().isoformat()


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db), _: User = Depends(current_user)):
    claims = db.scalars(select(Claim)).all()
    by_status = Counter(c.status for c in claims)
    finding_rows = db.execute(select(Finding.claim_id, Finding.code, Finding.severity)).all()
    serious_claims = {cid for cid, _, sev in finding_rows if sev != "low"}
    processed = [c for c in claims if c.status not in IN_PROGRESS and c.status != Status.FAILED]
    awaiting = [c for c in claims if c.status in (Status.NEEDS_REVIEW, Status.READY)]

    today = datetime.now(timezone.utc).date()
    days = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
    received = Counter(_day(c.created_at) for c in claims)
    submitted = Counter(_day(e.at) for e in db.scalars(
        select(ClaimEvent).where(ClaimEvent.type.in_(("transition", "decision"))))
        if (e.data or {}).get("to") == Status.SUBMITTED and (e.data or {}).get("from") == Status.APPROVED)

    runs = db.scalars(select(AgentRun)).all()
    agents: dict[str, dict] = {}
    for r in runs:
        a = agents.setdefault(r.agent, {"agent": r.agent, "calls": 0, "tokens": 0, "latency_ms": 0})
        a["calls"] += 1
        a["tokens"] += r.input_tokens + r.output_tokens
        a["latency_ms"] += r.latency_ms
    for a in agents.values():
        a["avg_latency_ms"] = round(a.pop("latency_ms") / a["calls"])
    timed = [c.processed_seconds for c in claims if c.processed_seconds]

    revenue = _revenue(db, today)
    recent = db.execute(select(ClaimEvent, Claim.number).join(Claim, Claim.id == ClaimEvent.claim_id)
                        .order_by(ClaimEvent.id.desc()).limit(14)).all()
    return {
        "kpis": {
            "total_claims": len(claims),
            "awaiting_review": len(awaiting),
            "in_progress": sum(by_status[s] for s in IN_PROGRESS),
            "submitted": sum(by_status[s] for s in SENT),
            "rejected": by_status[Status.REJECTED],
            "failed": by_status[Status.FAILED],
            "clean_rate": round(100 * sum(1 for c in processed if c.id not in serious_claims) / len(processed), 1) if processed else None,
            "billed_submitted": round(sum(c.total_charge or 0 for c in claims if c.status in SENT), 2),
            "value_at_risk": round(sum(c.total_charge or 0 for c in awaiting if c.risk == "high"), 2),
            "avg_processing_seconds": round(sum(timed) / len(timed), 1) if timed else None,
            "ai_calls": len(runs),
            "ai_tokens": sum(r.input_tokens + r.output_tokens for r in runs),
        },
        "throughput": [{"day": d, "received": received[d], "submitted": submitted[d]} for d in days],
        "by_status": dict(by_status),
        "by_risk": dict(Counter(c.risk for c in claims if c.risk)),
        "top_findings": [{"code": code, "count": n} for code, n in Counter(code for _, code, _ in finding_rows).most_common(8)],
        "agents": sorted(agents.values(), key=lambda a: -a["calls"]),
        "revenue": revenue,
        "recent": [{"claim_id": e.claim_id, "claim_number": number, "type": e.type, "actor": e.actor,
                    "message": e.message, "at": iso(e.at)} for e, number in recent],
    }


def _revenue(db: Session, today) -> dict:
    remits = db.scalars(select(Remittance)).all()
    denials = db.scalars(select(Denial)).all()
    appeals = db.scalars(select(Appeal)).all()
    adjudicated = {r.claim_id for r in remits if r.kind in ("original", "imported")}
    denied_claims = {d.claim_id for d in denials if d.group_code != "PR"}
    open_ = [d for d in denials if d.status in DENIAL_OPEN]
    decided = [a for a in appeals if a.status in ("overturned", "upheld")]
    billed = sum(c.total_charge or 0 for c in db.scalars(select(Claim).where(Claim.id.in_(adjudicated))))
    by_reason: dict[str, dict] = {}
    for d in denials:
        if d.group_code == "PR":
            continue
        row = by_reason.setdefault(f"{d.group_code}-{d.reason_code}", {"code": f"{d.group_code}-{d.reason_code}",
                                                                      "category": d.category, "count": 0, "amount": 0.0})
        row["count"] += 1
        row["amount"] = round(row["amount"] + d.amount, 2)
    return {
        "billed": round(billed, 2),
        "paid": round(sum(r.paid for r in remits), 2),
        "patient_responsibility": round(sum(d.amount for d in denials if d.group_code == "PR"), 2),
        "denial_rate": round(100 * len(denied_claims) / len(adjudicated), 1) if adjudicated else None,
        "denied_amount": round(sum(d.amount for d in denials if d.group_code != "PR"), 2),
        "open_denials": len(open_),
        "open_amount": round(sum(d.amount for d in open_), 2),
        "expected_recovery": round(sum(d.expected_recovery or 0 for d in open_), 2),
        "due_soon": sum(1 for d in open_ if d.deadline and (datetime.fromisoformat(d.deadline).date() - today).days <= 14),
        "recovered": round(sum(d.recovered for d in denials), 2),
        "written_off": round(sum(d.amount for d in denials if d.status == "written_off"), 2),
        "appeals_sent": sum(1 for a in appeals if a.sent_at),
        "appeal_win_rate": round(100 * sum(a.status == "overturned" for a in decided) / len(decided), 1) if decided else None,
        "by_reason": sorted(by_reason.values(), key=lambda r: -r["amount"]),
    }


@router.get("/audit")
def audit_log(limit: int = 200, db: Session = Depends(get_db), _: User = Depends(require_role("biller"))):
    rows = db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000))).all()
    return [{"id": r.id, "at": iso(r.at), "actor": r.actor, "action": r.action, "entity_type": r.entity_type,
             "entity_id": r.entity_id, "details": r.details, "hash": r.hash, "prev_hash": r.prev_hash} for r in rows]


@router.get("/audit/verify")
def audit_verify(db: Session = Depends(get_db), _: User = Depends(require_role("biller"))):
    return audit.verify(db)


@router.get("/events")
async def stream(request: Request, _: User = Depends(current_user)):
    sub = events.subscribe()

    async def generate():
        try:
            yield "retry: 3000\n\n"
            while not await request.is_disconnected():
                try:
                    event = await asyncio.wait_for(sub[1].get(), timeout=15)
                    yield f"data: {json.dumps(event)}\n\n"
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # stops proxies closing an idle connection
        finally:
            events.unsubscribe(sub)

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
