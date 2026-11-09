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
    # A document belongs to a claim, a prior authorization request or an encounter being coded.
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id"), index=True)
    authorization_id: Mapped[int | None] = mapped_column(ForeignKey("authorizations.id"), index=True)
    encounter_id: Mapped[int | None] = mapped_column(ForeignKey("encounters.id"), index=True)
    filename: Mapped[str] = mapped_column(String(300))
    path: Mapped[str] = mapped_column(String(500))
    kind: Mapped[str | None] = mapped_column(String(40))  # claim_form | invoice | insurance_card | ...
    pages: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text, default="")
    fields: Mapped[dict] = mapped_column(JSON, default=dict)  # what the agent read from this document

    claim: Mapped[Claim | None] = relationship(back_populates="documents")


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
    kind: Mapped[str] = mapped_column(String(20))  # extract | validate | submit | remit | triage | ... | auth_*
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id"), index=True)  # None for auth_* jobs
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
    authorization_id: Mapped[int | None] = mapped_column(ForeignKey("authorizations.id"), index=True)
    encounter_id: Mapped[int | None] = mapped_column(ForeignKey("encounters.id"), index=True)
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
    payload: Mapped[dict] = mapped_column(JSON, default=dict)  # the claim as our system holds it
    x12: Mapped[str | None] = mapped_column(Text)  # the 837P file sent: what the payer actually reads
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


class Authorization(Base):
    """A prior authorization request for an ordered service: from the order packet to the
    payer's decision. See app/authorizations.py for the lifecycle."""

    __tablename__ = "authorizations"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    urgency: Mapped[str] = mapped_column(String(10), default="routine")  # routine | urgent
    source: Mapped[str] = mapped_column(String(20), default="upload")  # upload | sample | demo-history
    case_id: Mapped[str | None] = mapped_column(String(40))  # sample packet it came from (for its addendum)
    patient_name: Mapped[str | None] = mapped_column(String(200))
    payer_name: Mapped[str | None] = mapped_column(String(200))
    member_id: Mapped[str | None] = mapped_column(String(60))
    services: Mapped[list] = mapped_column(JSON, default=list)  # [{cpt, description, units}]
    scheduled_date: Mapped[str | None] = mapped_column(String(10))
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    requirement: Mapped[dict] = mapped_column(JSON, default=dict)  # {required, eligible, reasons, policy_id}
    assessment: Mapped[dict] = mapped_column(JSON, default=dict)  # {criteria, missing, action, reasons, retrieval}
    draft: Mapped[dict] = mapped_column(JSON, default=dict)  # the request letter (cited paragraphs, checklist)
    letter: Mapped[dict] = mapped_column(JSON, default=dict)  # what was sent, after edits
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    fhir_request: Mapped[dict] = mapped_column(JSON, default=dict)
    fhir_response: Mapped[dict] = mapped_column(JSON, default=dict)
    decision: Mapped[dict] = mapped_column(JSON, default=dict)  # as read from the payer's response
    auth_number: Mapped[str | None] = mapped_column(String(40))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # payer's deadline to answer
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list[Document]] = relationship(order_by="Document.id", foreign_keys="Document.authorization_id")
    events: Mapped[list["AuthEvent"]] = relationship(order_by="AuthEvent.id")


class AuthEvent(Base):
    """An authorization request's timeline, like ClaimEvent for claims."""

    __tablename__ = "auth_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    authorization_id: Mapped[int] = mapped_column(ForeignKey("authorizations.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))  # transition | step | decision
    actor: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Encounter(Base):
    """An outpatient visit to code: from the visit note to the codes a coder approved and the
    claim generated from them. See app/coding.py for the lifecycle."""

    __tablename__ = "encounters"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    status: Mapped[str] = mapped_column(String(30), index=True)
    source: Mapped[str] = mapped_column(String(20), default="upload")
    case_id: Mapped[str | None] = mapped_column(String(40))
    patient_name: Mapped[str | None] = mapped_column(String(200))
    payer_name: Mapped[str | None] = mapped_column(String(200))
    provider_name: Mapped[str | None] = mapped_column(String(200))
    service_date: Mapped[str | None] = mapped_column(String(10))
    extracted: Mapped[dict] = mapped_column(JSON, default=dict)
    suggestion: Mapped[dict] = mapped_column(JSON, default=dict)  # the Coding Agent's codes, E/M, edits, evidence
    final: Mapped[dict] = mapped_column(JSON, default=dict)  # the codes a coder approved, and what they changed
    claim_id: Mapped[int | None] = mapped_column(ForeignKey("claims.id"))
    coded_by: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    coded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    documents: Mapped[list[Document]] = relationship(order_by="Document.id", foreign_keys="Document.encounter_id")
    events: Mapped[list["EncounterEvent"]] = relationship(order_by="EncounterEvent.id")


class EncounterEvent(Base):
    __tablename__ = "encounter_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    encounter_id: Mapped[int] = mapped_column(ForeignKey("encounters.id"), index=True)
    type: Mapped[str] = mapped_column(String(20))
    actor: Mapped[str] = mapped_column(String(100))
    message: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FollowUp(Base):
    """Accounts receivable follow-up on a claim the payer hasn't answered: every status check
    (the 276 sent, the 277 received, what the A/R agent recommended) and what was done."""

    __tablename__ = "ar_followups"

    id: Mapped[int] = mapped_column(primary_key=True)
    claim_id: Mapped[int] = mapped_column(ForeignKey("claims.id"), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | waiting | resolved
    checks: Mapped[list] = mapped_column(JSON, default=list)
    recommended_action: Mapped[str | None] = mapped_column(String(30))
    action_taken: Mapped[str | None] = mapped_column(String(30))
    next_check_on: Mapped[str | None] = mapped_column(String(10))
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    claim: Mapped[Claim] = relationship()


class Statement(Base):
    """A patient statement: what the patient owes after insurance, with a plain-language reason
    per line, the reminders sent, any payment plan, and payments received."""

    __tablename__ = "patient_statements"

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(40), unique=True)
    patient_key: Mapped[str] = mapped_column(String(60), index=True)  # member ID, normalized
    patient_name: Mapped[str] = mapped_column(String(200))
    items: Mapped[list] = mapped_column(JSON, default=list)  # [{denial_id, claim_number, service_date, code, reason, amount}]
    total: Mapped[float] = mapped_column(Float, default=0)
    paid: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | partial | plan | paid | review
    stage: Mapped[str] = mapped_column(String(20), default="statement")  # statement | reminder | final_notice | review
    issued_on: Mapped[str] = mapped_column(String(10))
    due_on: Mapped[str] = mapped_column(String(10))
    plan: Mapped[dict] = mapped_column(JSON, default=dict)  # {installments, schedule: [{due_on, amount, paid}]}
    letters: Mapped[list] = mapped_column(JSON, default=list)  # [{stage, path, at}]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class PatientPayment(Base):
    __tablename__ = "patient_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    statement_id: Mapped[int] = mapped_column(ForeignKey("patient_statements.id"), index=True)
    amount: Mapped[float] = mapped_column(Float)
    method: Mapped[str] = mapped_column(String(20))  # card | cash | check | ach
    reference: Mapped[str | None] = mapped_column(String(60))
    received_on: Mapped[str] = mapped_column(String(10))
    recorded_by: Mapped[str] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class BankDeposit(Base):
    """A line from an imported bank statement, and what reconciliation matched it to."""

    __tablename__ = "bank_deposits"
    __table_args__ = (UniqueConstraint("bank_reference"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    posted_on: Mapped[str] = mapped_column(String(10))
    amount: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    bank_reference: Mapped[str] = mapped_column(String(60))
    trace_number: Mapped[str | None] = mapped_column(String(60), index=True)  # from the ACH addenda (TRN*1*...)
    status: Mapped[str] = mapped_column(String(30), default="unmatched")  # matched | amount_differs | unmatched | resolved
    remittance_ids: Mapped[list] = mapped_column(JSON, default=list)
    note: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------- Front office: an Indian hospital OPD ----------

class OpdPatient(Base):
    """A registered patient. Found by mobile number first; one number often serves a whole family."""

    __tablename__ = "opd_patients"

    id: Mapped[int] = mapped_column(primary_key=True)
    uhid: Mapped[str] = mapped_column(String(20), unique=True)  # the hospital's patient ID
    name: Mapped[str] = mapped_column(String(200))
    sex: Mapped[str] = mapped_column(String(1))  # M | F | O
    dob: Mapped[str | None] = mapped_column(String(10))
    age_years: Mapped[float | None] = mapped_column(Float)  # when only an age was given, as of registered_on
    phone: Mapped[str] = mapped_column(String(10), index=True)
    abha: Mapped[str | None] = mapped_column(String(14), unique=True)  # Ayushman Bharat Health Account number, digits
    city: Mapped[str | None] = mapped_column(String(100))
    registered_on: Mapped[str] = mapped_column(String(10))
    registered_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Doctor(Base):
    __tablename__ = "doctors"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    department: Mapped[str] = mapped_column(String(40), index=True)
    qualification: Mapped[str] = mapped_column(String(100))
    room: Mapped[str] = mapped_column(String(20))
    fee: Mapped[float] = mapped_column(Float)  # consultation fee, rupees
    days: Mapped[list] = mapped_column(JSON, default=list)  # OPD weekdays, 0 = Monday
    opd_start: Mapped[str] = mapped_column(String(5))
    opd_end: Mapped[str] = mapped_column(String(5))
    minutes_per_patient: Mapped[int] = mapped_column(Integer, default=10)
    languages: Mapped[list] = mapped_column(JSON, default=list)


class OpdVisit(Base):
    """One trip to the OPD: the complaint, what intake found, and the token.

    triaged -> waiting -> in_consult -> done; or skipped (not there when called, can be recalled),
    cancelled, or emergency (sent to casualty instead of a token)."""

    __tablename__ = "opd_visits"
    __table_args__ = (UniqueConstraint("doctor_id", "visit_date", "token"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    number: Mapped[str] = mapped_column(String(30), unique=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("opd_patients.id"), index=True)
    doctor_id: Mapped[int | None] = mapped_column(ForeignKey("doctors.id"), index=True)
    visit_date: Mapped[str] = mapped_column(String(10), index=True)
    token: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), default="triaged", index=True)
    complaint: Mapped[str] = mapped_column(Text)
    intake: Mapped[dict] = mapped_column(JSON, default=dict)  # the Intake Agent's note, red flags, department
    suggestion: Mapped[dict] = mapped_column(JSON, default=dict)  # doctors considered and why
    override_reason: Mapped[str | None] = mapped_column(Text)  # staff chose another doctor, or cleared a red flag
    fee: Mapped[float] = mapped_column(Float, default=0)
    fee_note: Mapped[str | None] = mapped_column(String(200))
    payment_mode: Mapped[str | None] = mapped_column(String(10))  # cash | upi | card
    created_by: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    booked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    called_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    patient: Mapped[OpdPatient] = relationship()
    doctor: Mapped[Doctor | None] = relationship()
