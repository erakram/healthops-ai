"""Database tables.

A claim is the unit of work. Its documents are read by the extraction agent, the
results are merged into `claim.extracted` (one canonical record), and the validator
writes `findings`. Every state change is a ClaimEvent; every action by a person or
an agent is also written to the tamper-evident AuditLog.
"""

from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(30))  # intake | biller | admin
    password_hash: Mapped[str] = mapped_column(String(300))


class Claim(Base):
    __tablename__ = "claims"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    risk: Mapped[str | None] = mapped_column(String(10))  # low | medium | high, set by the validator
    # Copied out of `extracted` so lists can be filtered and sorted without parsing JSON.
    patient_name: Mapped[str | None] = mapped_column(String(200))
    payer_name: Mapped[str | None] = mapped_column(String(200))
    total_charge: Mapped[float | None] = mapped_column(Float)
    service_date: Mapped[str | None] = mapped_column(String(10))  # ISO date
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)  # field -> {value, confidence, evidence}
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | sample | demo-history
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    processed_seconds: Mapped[float | None] = mapped_column(Float)  # upload -> ready for a human

    documents: Mapped[list["Document"]] = relationship(back_populates="claim", order_by="Document.id")
    findings: Mapped[list["Finding"]] = relationship(back_populates="claim", order_by="Finding.id")
    events: Mapped[list["ClaimEvent"]] = relationship(back_populates="claim", order_by="ClaimEvent.id")


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str | None] = mapped_column(String(40))  # claim_form | invoice | insurance_card | ...
    pages: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)  # what the agent read from this document

    claim: Mapped[Claim] = relationship(back_populates="documents")


class Finding(Base):
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    severity: Mapped[str] = mapped_column(String(10))  # critical | high | medium | low
    source: Mapped[str] = mapped_column(String(10))  # rule | ai
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSON, default=list)  # [{document_id, page, bbox, text}]

    claim: Mapped[Claim] = relationship(back_populates="findings")


class ClaimEvent(Base):
    """The claim's timeline: state changes, agent steps and human decisions."""

    __tablename__ = "claim_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # transition | step | decision | correction
    actor: Mapped[str] = mapped_column(String(100))  # a person's email or an agent's name
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    claim: Mapped[Claim] = relationship(back_populates="events")


class Correction(Base):
    """A reviewer changed a value the agent extracted. Kept so it can become a test case."""

    __tablename__ = "corrections"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    field: Mapped[str] = mapped_column(String(60))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Job(Base):
    """A queued pipeline step. The idempotency key stops the same step being queued twice."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))  # extract | validate | submit | remit | triage | draft_appeal | appeal_decision
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # e.g. {"denial_id": 3}
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)  # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    run_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AgentRun(Base):
    """One LLM call: which agent, which model, tokens and latency. Feeds the cost and latency views."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id"), index=True)
    agent: Mapped[str] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    ok: Mapped[bool] = mapped_column(default=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Submission(Base):
    """A claim sent to the (mock) payer. The idempotency key ("submit:<claim>:<frequency>:<n>")
    means a retried job can't send the same claim twice. Frequency 1 is the original claim,
    7 a replacement (corrected) claim."""

    __tablename__ = "submissions"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    idempotency_key: Mapped[str] = mapped_column(String(100), unique=True)
    payer_reference: Mapped[str] = mapped_column(String(40))
    frequency: Mapped[str] = mapped_column(String(1), default="1")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # exactly what the payer received
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Remittance(Base):
    """An 835 from a payer, kept verbatim. `parsed` is what our parser read from it, so what
    the UI shows can always be traced back to the raw segments."""

    __tablename__ = "remittances"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # original | corrected | appeal | imported
    payer_name: Mapped[str] = mapped_column(String(200))
    trace_number: Mapped[str] = mapped_column(String(60))
    raw: Mapped[str] = mapped_column(Text)
    parsed: Mapped[dict] = mapped_column(JSON, default=dict)
    paid: Mapped[float] = mapped_column(Float, default=0)
    patient_responsibility: Mapped[float] = mapped_column(Float, default=0)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Denial(Base):
    """One adjustment on a remittance that someone has to act on: a payer denial, or a
    patient balance to bill. Grouped per claim, remittance and reason code."""

    __tablename__ = "denials"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), index=True)
    remittance_id: Mapped[int] = mapped_column(ForeignKey("remittances.id"))
    group_code: Mapped[str] = mapped_column(String(2))  # CO | PR | OA | PI
    reason_code: Mapped[str] = mapped_column(String(10))  # CARC
    remark_codes: Mapped[list] = mapped_column(JSON, default=list)  # RARC
    lines: Mapped[list] = mapped_column(JSON, default=list)  # [{cpt, modifiers, charge, amount}]
    amount: Mapped[float] = mapped_column(Float)  # adjusted amount in dispute
    category: Mapped[str] = mapped_column(String(30))
    status: Mapped[str] = mapped_column(String(30), index=True)  # see core/denials.py
    recommended_action: Mapped[str | None] = mapped_column(String(30))
    action_taken: Mapped[str | None] = mapped_column(String(30))
    deadline: Mapped[str | None] = mapped_column(String(10))  # ISO date the action must happen by
    win_probability: Mapped[float | None] = mapped_column(Float)
    expected_recovery: Mapped[float | None] = mapped_column(Float)
    recovered: Mapped[float] = mapped_column(Float, default=0)
    policy_id: Mapped[str | None] = mapped_column(String(20))
    triage: Mapped[dict] = mapped_column(JSON, default=dict)  # reasoning, criteria checks, retrieval
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    claim: Mapped[Claim] = relationship()
    remittance: Mapped[Remittance] = relationship()


class Appeal(Base):
    """An appeal letter for one denial: the agent's draft (every paragraph with its verified
    citations), what the biller changed, and the payer's decision."""

    __tablename__ = "appeals"
    __table_args__ = (UniqueConstraint("denial_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    denial_id: Mapped[int] = mapped_column(ForeignKey("denials.id"))
    status: Mapped[str] = mapped_column(String(20))  # drafting | draft | sent | overturned | upheld
    draft: Mapped[dict] = mapped_column(JSON, default=dict)  # {paragraphs, removed, stats}
    letter: Mapped[dict] = mapped_column(JSON, default=dict)  # final paragraphs as sent
    edited_by: Mapped[str | None] = mapped_column(String(200))
    sent_by: Mapped[str | None] = mapped_column(String(200))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    outcome_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    denial: Mapped[Denial] = relationship()


class AuditLog(Base):
    """Append-only. Each row stores a hash of its content plus the previous row's hash,
    so editing or deleting any row breaks every hash after it."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(100))
    action: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str] = mapped_column(String(30))
    entity_id: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
