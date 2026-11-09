// Typed client for the FastAPI backend. Requests go to /api/*, which Next.js rewrites to
// the backend, so the session cookie is same-origin.

export type Role = "intake" | "biller" | "admin";
export type ClaimStatus =
  | "received" | "extracting" | "validating" | "needs_review" | "ready"
  | "approved" | "rejected" | "submitted" | "paid" | "denied" | "appealed" | "closed" | "failed";
export type Risk = "low" | "medium" | "high";
export type Severity = "critical" | "high" | "medium" | "low";

export interface User { id: number; email: string; name: string; role: Role }

export interface ClaimSummary {
  id: number;
  number: string;
  status: ClaimStatus;
  risk: Risk | null;
  patient_name: string | null;
  payer_name: string | null;
  total_charge: number | null;
  service_date: string | null;
  source: "upload" | "sample" | "demo-history";
  finding_count: number;
  created_at: string;
  updated_at: string;
}

export interface Evidence {
  document_id: number;
  page: number;
  bbox: [number, number, number, number];
  text: string;
  label?: string;
}

export interface ExtractedField {
  value: unknown;
  raw?: unknown;
  confidence: number;
  evidence?: Evidence | null;
  corrected_by?: string;
}

export interface LineItem { cpt: string; description: string; units: number; charge: number; evidence?: Evidence | null }
export interface CodeItem { code: string; evidence?: Evidence | null }

export interface Finding {
  id: number;
  code: string;
  severity: Severity;
  source: "rule" | "ai";
  title: string;
  detail: string;
  evidence: Evidence[];
}

export interface ClaimEvent {
  id: number;
  type: "transition" | "step" | "decision" | "correction";
  actor: string;
  message: string;
  data: Record<string, unknown>;
  at: string;
}

export interface DocumentInfo { id: number; filename: string; kind: string | null; pages: number; label: string }

export interface FieldSchema {
  [kind: string]: {
    label: string;
    fields: { name: string; label: string; type: string }[];
    lists: Record<string, string>;
  };
}

export interface ClaimDetail extends ClaimSummary {
  processed_seconds: number | null;
  extracted: Record<string, Record<string, ExtractedField>>;
  documents: DocumentInfo[];
  findings: Finding[];
  events: ClaimEvent[];
  agent_runs: { agent: string; model: string; input_tokens: number; output_tokens: number; latency_ms: number; at: string }[];
  submission: { payer_reference: string; at: string } | null;
  submissions: { payer_reference: string; frequency: string; at: string }[];
  remittances: Remittance[];
  denials: DenialSummary[];
  financials: {
    billed: number | null; allowed: number; paid: number; patient_responsibility: number;
    recovered: number; written_off: number;
  } | null;
  schema: FieldSchema;
}

/* ---------- Remittances, denials and appeals ---------- */

export interface Adjustment { group: string; code: string; amount: number }
export interface RemitLine {
  cpt: string; modifiers: string[]; charge: number; paid: number; units: number; service_date: string | null;
  allowed: number | null; adjustments: Adjustment[]; remarks: string[];
}
export interface RemitClaim {
  claim_number: string; status_code: string; status: string; charge: number; paid: number;
  patient_responsibility: number; payer_control_number: string; frequency: string;
  patient: { last?: string; first?: string; member_id?: string }; service_date: string | null;
  adjustments: Adjustment[]; lines: RemitLine[];
}
export interface Remittance {
  id: number; kind: "original" | "corrected" | "appeal" | "imported"; payer_name: string; trace_number: string;
  paid: number; patient_responsibility: number; received_at: string; claim: RemitClaim;
  payment: { amount: number; method: string; date: string | null; trace?: string }; warnings: string[];
}

export type DenialStatus =
  | "triage" | "open" | "appeal_sent" | "resubmitted" | "upheld" | "overturned"
  | "paid_on_resubmission" | "patient_billed" | "written_off" | "superseded";
export type DenialAction = "appeal" | "corrected_claim" | "bill_patient" | "write_off" | "manual_review";

export interface DenialSummary {
  id: number; claim_id: number; claim_number: string; patient_name: string | null; payer_name: string | null;
  service_date: string | null; code: string; group: string; group_label: string | null; reason: string;
  remarks: { code: string; text: string }[]; category: string; category_label: string; amount: number;
  lines: { cpt: string; modifiers: string[]; charge: number; amount: number }[];
  status: DenialStatus; status_label: string; recommended_action: DenialAction | null; recommended_label: string | null;
  action_taken: string | null; deadline: string | null; days_left: number | null; win_probability: number | null;
  expected_recovery: number | null; recovered: number; policy_id: string | null; appeal_status: AppealStatus | null;
  created_at: string; closed_at: string | null;
}

export interface Criterion {
  id: string; text: string; met: boolean; model_said_met?: boolean; quote: string; explanation?: string;
  evidence?: Evidence | null;
}
export interface Citation { source: string; label: string; quote: string; verified: boolean; evidence: Evidence | null }
export interface Paragraph {
  kind: "argument" | "request"; text: string; citations: Citation[]; dropped_citations?: number;
  source_index?: number | null; edited?: boolean;
}
export type AppealStatus = "drafting" | "draft" | "sent" | "overturned" | "upheld" | "failed";
export interface Appeal {
  id: number; status: AppealStatus;
  draft: {
    paragraphs?: Paragraph[]; removed?: { text: string; reason: string }[]; attachments?: string[];
    stats?: { citations: number; verified_citations: number; citation_rate: number | null; paragraphs: number;
              removed_paragraphs: number; invented_fact_paragraphs: number };
    header?: { date: string; from: string; to: string; subject: string; reference: [string, string][];
               salutation: string; closing: string; signature: string };
    decision?: { satisfied: string[]; missing: string[] }; historical?: boolean; error?: string;
    checklist?: { id: string; source: string; quote: string; cited: boolean }[];
  };
  letter: { paragraphs?: Paragraph[]; attachments?: string[] };
  edited_by: string | null; sent_by: string | null; sent_at: string | null; decided_at: string | null;
  outcome_reason: string | null;
}
export interface Policy {
  id: string; title: string; kind: string; cpt_codes: string[]; carc_codes: string[]; body: string;
  criteria: { id: string; text: string }[];
}
export interface DenialDetail extends DenialSummary {
  triage: {
    category?: string; action?: DenialAction; reasons?: string[]; deadline?: string | null; policy_id?: string | null;
    retrieval?: { id: string; title: string; score: number; matched: string[] }[]; criteria?: Criterion[];
    summary?: string; fix?: { cpt: string; add_modifier: string } | null;
  };
  claim: ClaimSummary; documents: DocumentInfo[]; remittance: Remittance; remittances: Remittance[];
  appeal: Appeal | null; policy: Policy | null; letter_text: string | null; events: ClaimEvent[];
  agent_runs: { agent: string; model: string; input_tokens: number; output_tokens: number; latency_ms: number }[];
  other_denials: DenialSummary[]; actions: Record<DenialAction, string>;
}
export interface DenialsSummary {
  open: number; open_amount: number; expected_recovery: number; due_soon: number; waiting: number; recovered: number;
}

export interface Dashboard {
  kpis: {
    total_claims: number;
    awaiting_review: number;
    in_progress: number;
    submitted: number;
    rejected: number;
    failed: number;
    clean_rate: number | null;
    billed_submitted: number;
    value_at_risk: number;
    avg_processing_seconds: number | null;
    ai_calls: number;
    ai_tokens: number;
  };
  throughput: { day: string; received: number; submitted: number }[];
  by_status: Partial<Record<ClaimStatus, number>>;
  by_risk: Partial<Record<Risk, number>>;
  top_findings: { code: string; count: number }[];
  agents: { agent: string; calls: number; tokens: number; avg_latency_ms: number }[];
  recent: { claim_id: number; claim_number: string; type: string; actor: string; message: string; at: string }[];
  revenue: {
    billed: number; paid: number; patient_responsibility: number; denial_rate: number | null; denied_amount: number;
    open_denials: number; open_amount: number; expected_recovery: number; due_soon: number; recovered: number;
    written_off: number; appeals_sent: number; appeal_win_rate: number | null;
    by_reason: { code: string; category: string; count: number; amount: number }[];
  };
}

export interface AuditEntry {
  id: number;
  at: string;
  actor: string;
  action: string;
  entity_type: string;
  entity_id: string;
  details: Record<string, unknown>;
  hash: string;
  prev_hash: string;
}

export interface SampleCase {
  case_id: string; title: string; scenario: string; patient: string; files: string[];
  stage: "intake" | "after_submission";
}

/* ---------- Prior authorization ---------- */

export type AuthStatus =
  | "received" | "reading" | "checking" | "not_required" | "ineligible" | "needs_documentation" | "ready"
  | "submitted" | "approved" | "pended" | "denied" | "withdrawn" | "failed";
export type AuthAction = "submit" | "request_documentation" | "do_not_submit" | "none" | "ineligible";

export interface AuthSummary {
  id: number; number: string; status: AuthStatus; status_label: string; urgency: "routine" | "urgent";
  source: string; patient_name: string | null; payer_name: string | null; member_id: string | null;
  services: { cpt: string; description: string | null; units: number }[]; scheduled_date: string | null;
  action: AuthAction | null; action_label: string | null; missing: number; attempts: number; auth_number: string | null;
  submitted_at: string | null; due_at: string | null; hours_left: number | null; decided_at: string | null;
  turnaround_seconds: number | null; has_sample_addendum: boolean; created_at: string; updated_at: string;
}

export interface AuthCriterion extends Criterion { source?: string | null }

export interface AuthDetail extends AuthSummary {
  extracted: Record<string, Record<string, ExtractedField>>;
  requirement: {
    required?: boolean; eligible?: boolean; reasons?: string[]; policy_id?: string | null; required_cpts?: string[];
    eligibility?: { active: boolean; terminated_on: string | null; payer: string };
  };
  assessment: {
    action?: AuthAction; reasons?: string[]; criteria?: AuthCriterion[]; summary?: string; policy_id?: string | null;
    missing?: { id: string; text: string; ask: string; unverified_quote: string | null }[];
  };
  draft: Appeal["draft"] & { facts?: Record<string, unknown> };
  letter: Appeal["letter"];
  decision: {
    decision?: "approved" | "pended" | "denied"; review_action?: string; outcome?: string; auth_number?: string | null;
    valid_from?: string | null; valid_to?: string | null; disposition?: string; notes?: string[];
    satisfied?: string[]; missing?: string[];
  };
  fhir_request: Record<string, unknown>; fhir_response: Record<string, unknown>;
  policy: Policy | null;
  documents: (DocumentInfo & { addendum?: boolean })[];
  events: ClaimEvent[];
  agent_runs: { agent: string; model: string; input_tokens: number; output_tokens: number; latency_ms: number }[];
}

export interface AuthsSummary {
  open: number; needs_documentation: number; with_payer: number; approved: number; approval_rate: number | null;
  first_pass_approval_rate: number | null; avg_turnaround_seconds: number | null; not_required: number;
}

export interface AuthSample { case_id: string; title: string; scenario: string; patient: string; files: string[]; has_addendum: boolean }

/* ---------- Medical coding ---------- */

export type EncStatus = "received" | "reading" | "coding" | "review" | "coded" | "billed" | "failed";
export interface CodeHit { code: string; description: string; score: number | null }
export interface SuggestedDx {
  code: string; description: string; documented: string | null; quote: string | null; evidence: Evidence | null;
  reason: string | null; candidates: CodeHit[];
}
export interface CptLine { code: string; modifiers: string[]; units: number }
export interface SuggestedPx extends CptLine {
  description: string; documented: string | null; quote: string | null; evidence: Evidence | null; reason: string | null;
  candidates: CodeHit[];
}
export interface CodingEdit {
  code: string; severity: "high" | "medium" | "low"; target: string; title: string; detail: string;
  fix: { replace?: string; add_modifier?: string; remove?: string; units?: number } | null;
}
export interface MdmElement { level?: string; quote?: string; reason?: string; evidence?: Evidence | null }
export interface EncounterSummary {
  id: number; number: string; status: EncStatus; status_label: string; source: string; patient_name: string | null;
  payer_name: string | null; provider_name: string | null; service_date: string | null; codes: string[]; edits: number;
  agreed: boolean | null; changes: number; claim_id: number | null; coded_by: string | null; coded_at: string | null;
  created_at: string;
}
export interface EncounterDetail extends EncounterSummary {
  suggestion: {
    diagnoses?: SuggestedDx[]; procedures?: SuggestedPx[]; edits?: CodingEdit[];
    em?: { code: string; patient_status: string; mdm_level: string; time_minutes: number | null; time_code: string | null;
           basis: string; explanation: string } | null;
    documented?: { visit?: { type?: string; patient_status?: string; evidence?: Evidence | null };
                   time?: { value?: number | null; quote?: string; evidence?: Evidence | null };
                   mdm?: Record<"problems" | "data" | "risk", MdmElement>;
                   separate_em?: { documented?: boolean; quote?: string; evidence?: Evidence | null } };
    stats?: { diagnoses: number; procedures: number; rejected_choices: number; grounded: number; quotes: number };
  };
  final: { icd?: { code: string; description: string }[]; cpt?: (CptLine & { description: string })[]; edits?: CodingEdit[];
           changes?: { kind: string; change: string; code: string }[]; reason?: string | null; agreed?: boolean };
  documents: DocumentInfo[]; events: ClaimEvent[];
  agent_runs: { agent: string; model: string; input_tokens: number; output_tokens: number; latency_ms: number }[];
}
export interface CodingSample { case_id: string; title: string; scenario: string; patient: string; files: string[] }

/* ---------- Accounts receivable, patient billing, reconciliation ---------- */

export interface ArRow {
  claim_id: number; claim_number: string; patient_name: string | null; payer_name: string | null; service_date: string | null;
  amount: number; submitted_on: string; age_days: number; bucket: string; due_for_follow_up: boolean; filing_deadline: string;
  days_to_filing_deadline: number; priority: number; followup_status: string | null; recommended_action: string | null;
  last_status: ArStatus | null; next_check_on: string | null;
}
export interface ArStatus {
  category: string; code: string; category_text: string; code_text: string; effective: string | null;
  paid_amount: number | null; check_number: string | null; message: string | null;
}
export interface ArCheck {
  at: string; by: string; x12_276: string; x12_277: string; status: ArStatus; payer_claim_number: string | null;
  action: string; reasons: string[]; note: { channel?: string; subject?: string; message: string; drafted_by: string; rejected_because?: string[] };
}
export interface ArDetail extends ArRow { claim_status: string; original_reference: string; checks: ArCheck[]; action_taken: string | null; actions: Record<string, string> }
export interface ArSummary {
  claims: number; amount: number; buckets: { bucket: string; count: number; amount: number }[]; due_for_follow_up: number;
  filing_risk: number; average_age_days: number | null;
}
export interface StatementRow {
  id: number; number: string; patient_name: string; total: number; paid: number; balance: number; status: string; stage: string;
  issued_on: string; due_on: string; items: { claim_number: string; service_date: string; code: string; description: string; reason: string; amount: number }[];
  plan: { installments?: number; schedule?: { due_on: string; amount: number; paid: boolean }[] };
  letters: { stage: string; at: string; index: number }[];
  payments?: { amount: number; method: string; reference: string | null; received_on: string; recorded_by: string }[];
}
export interface BillingSummary {
  statements: number; open: number; outstanding: number; collected: number; collection_rate: number | null; on_plan: number;
  needs_review: number; by_stage: Record<string, number>;
}
export interface Deposit {
  id: number; posted_on: string; amount: number; description: string; bank_reference: string; trace_number: string | null;
  status: "matched" | "amount_differs" | "unmatched" | "resolved"; note: string | null;
  remittances: { id: number; claim_id: number; payer: string; amount: number }[];
}
export interface Reconciliation {
  matched: number; amount_differs: number; unmatched: number; remittances_without_deposit: number; deposited: number;
  missing_deposits: { trace_number: string; amount: number; paid_on: string | null; payer: string; claims: number[] }[];
  deposits: Deposit[];
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}


/* ---------- OPD front desk ---------- */

export interface OpdPatient {
  id: number; uhid: string; name: string; sex: "M" | "F" | "O"; dob: string | null; age: number | null; age_label: string;
  phone: string; abha: string | null; city: string | null; registered_on: string;
}
export interface OpdPatientDetail extends OpdPatient {
  family: OpdPatient[];
  visits: { id: number; number: string; visit_date: string; status: string; token: number | null; doctor: string | null;
    department_name: string | null; summary: string; fee: number }[];
}
export interface DuplicateMatch { patient: OpdPatient; score: number; why: string[] }
export interface RedFlag { id: string; label: string; action: string; matched: string; sign?: string }
export interface DoctorOption {
  id: number; name: string; department: string; department_name: string; qualification: string; room: string; fee: number;
  days: string[]; hours: string; on_duty: boolean; next_on_duty: string | null; waiting: number; seen: number; wait_minutes: number;
  languages: string[]; seen_before?: string | null; fee_due?: number; fee_note?: string; reason?: string;
}
export interface OpdIntake {
  complaint: string; language: string | null; symptoms: { name: string; quote: string }[]; dropped_symptoms: number;
  duration: string | null; severity: "mild" | "moderate" | "severe" | "unknown"; summary_en: string | null; red_flags: RedFlag[];
  department: string; department_name: string; department_source: "agent" | "keywords" | "rule" | "default" | "demo";
  department_reason: string; keyword_department: string | null; agent_department: string | null;
  agent: { used: boolean; error: string | null; usage: { model: string; input_tokens: number; output_tokens: number; latency_ms: number } | null };
}
export interface OpdVisit {
  id: number; number: string; visit_date: string; token: number | null;
  status: "triaged" | "waiting" | "in_consult" | "done" | "skipped" | "cancelled" | "emergency";
  complaint: string; intake: OpdIntake;
  suggestion: { department: string; department_name: string; note: string | null; options: DoctorOption[]; suggested_doctor_id: number | null };
  override_reason: string | null; fee: number; fee_note: string | null; payment_mode: string | null; patient: OpdPatient;
  doctor: { id: number; name: string; room: string; department_name: string } | null;
}
export interface QueueDoctor extends DoctorOption {
  now: { id: number; token: number; name: string } | null;
  visits: { id: number; token: number; status: OpdVisit["status"]; name: string; age_label: string; sex: string; summary: string; fee: number }[];
}
export interface OpdSummary {
  date: string; day: string; tokens: number; waiting: number; in_consult: number; seen: number; emergencies: number;
  collected: number; free_follow_ups: number; new_patients: number; doctors_on_duty: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, { credentials: "same-origin", ...init });
  if (res.status === 401 && typeof window !== "undefined" && !path.startsWith("/api/auth")) {
    window.location.href = `/login?next=${encodeURIComponent(window.location.pathname)}`;
  }
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = await res.json();
      message = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {}
    throw new ApiError(res.status, message);
  }
  return res.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  me: () => request<User>("/api/auth/me"),
  demoAccounts: () => request<{ email: string; name: string; role: Role }[]>("/api/auth/demo-accounts"),
  login: (email: string, password: string) => request<User>("/api/auth/login", json({ email, password })),
  demoLogin: (email: string) => request<User>("/api/auth/demo-login", json({ email })),
  logout: () => request<{ ok: boolean }>("/api/auth/logout", { method: "POST" }),

  claims: (params: { status?: string; q?: string; risk?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(params).filter(([, v]) => v) as [string, string][]);
    return request<ClaimSummary[]>(`/api/claims?${qs}`);
  },
  claim: (id: number) => request<ClaimDetail>(`/api/claims/${id}`),
  upload: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<ClaimSummary>("/api/claims", { method: "POST", body: form });
  },
  samples: () => request<SampleCase[]>("/api/samples"),
  loadSample: (caseId: string) => request<ClaimSummary>(`/api/samples/${caseId}/load`, { method: "POST" }),
  decide: (id: number, action: "approve" | "reject", note?: string) =>
    request<ClaimDetail>(`/api/claims/${id}/decision`, json({ action, note })),
  correct: (id: number, document_type: string, field: string, value: string) =>
    request<ClaimDetail>(`/api/claims/${id}/fields`, { ...json({ document_type, field, value }), method: "PATCH" }),
  retry: (id: number) => request<ClaimDetail>(`/api/claims/${id}/retry`, { method: "POST" }),

  dashboard: () => request<Dashboard>("/api/dashboard"),

  denials: (view: "open" | "waiting" | "closed" | "all" = "open") => request<DenialSummary[]>(`/api/denials?view=${view}`),
  denialsSummary: () => request<DenialsSummary>("/api/denials/summary"),
  denial: (id: number) => request<DenialDetail>(`/api/denials/${id}`),
  denialAction: (id: number, action: "write_off" | "bill_patient" | "corrected_claim" | "draft_appeal", note?: string) =>
    request<DenialDetail>(`/api/denials/${id}/action`, json({ action, note })),
  saveAppeal: (id: number, paragraphs: { source_index: number | null; text: string }[]) =>
    request<DenialDetail>(`/api/appeals/${id}`, { ...json({ paragraphs }), method: "PUT" }),
  sendAppeal: (id: number, paragraphs: { source_index: number | null; text: string }[]) =>
    request<DenialDetail>(`/api/appeals/${id}/send`, json({ paragraphs })),
  importRemittance: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<{ matched: number; unknown_claims: string[]; remittances: Remittance[] }>("/api/remittances/import",
      { method: "POST", body: form });
  },
  authorizations: (view: "open" | "waiting" | "done" | "all" = "open") => request<AuthSummary[]>(`/api/authorizations?view=${view}`),
  authorizationsSummary: () => request<AuthsSummary>("/api/authorizations/summary"),
  authorization: (id: number) => request<AuthDetail>(`/api/authorizations/${id}`),
  authSamples: () => request<AuthSample[]>("/api/auth-samples"),
  loadAuthSample: (caseId: string) => request<AuthSummary>(`/api/auth-samples/${caseId}/load`, { method: "POST" }),
  createAuthorization: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<AuthSummary>("/api/authorizations", { method: "POST", body: form });
  },
  addAuthDocument: (id: number, file: File) => {
    const form = new FormData();
    form.append("files", file);
    return request<AuthDetail>(`/api/authorizations/${id}/documents`, { method: "POST", body: form });
  },
  addSampleAddendum: (id: number) => request<AuthDetail>(`/api/authorizations/${id}/sample-addendum`, { method: "POST" }),
  saveAuthLetter: (id: number, paragraphs: { source_index: number | null; text: string }[]) =>
    request<AuthDetail>(`/api/authorizations/${id}/letter`, { ...json({ paragraphs }), method: "PUT" }),
  submitAuthorization: (id: number, paragraphs: { source_index: number | null; text: string }[], note?: string) =>
    request<AuthDetail>(`/api/authorizations/${id}/submit`, json({ paragraphs, note })),
  withdrawAuthorization: (id: number, note: string) => request<AuthDetail>(`/api/authorizations/${id}/withdraw`, json({ note })),
  retryAuthorization: (id: number) => request<AuthDetail>(`/api/authorizations/${id}/retry`, { method: "POST" }),

  encounters: (view: "open" | "done" | "all" = "open") => request<EncounterSummary[]>(`/api/encounters?view=${view}`),
  encountersSummary: () => request<{ review: number; coded: number; billed: number; agreement_rate: number | null; edits_raised: number }>("/api/encounters/summary"),
  encounter: (id: number) => request<EncounterDetail>(`/api/encounters/${id}`),
  codingSamples: () => request<CodingSample[]>("/api/coding-samples"),
  loadCodingSample: (caseId: string) => request<EncounterSummary>(`/api/coding-samples/${caseId}/load`, { method: "POST" }),
  createEncounter: (files: File[]) => {
    const form = new FormData();
    files.forEach((f) => form.append("files", f));
    return request<EncounterSummary>("/api/encounters", { method: "POST", body: form });
  },
  searchIcd10: (q: string) => request<CodeHit[]>(`/api/codes/icd10?q=${encodeURIComponent(q)}`),
  cptList: () => request<{ code: string; description: string; price: number; max_units: number; em: boolean }[]>("/api/codes/cpt"),
  checkCodes: (id: number, icd: string[], cpt: CptLine[]) =>
    request<{ problems: string[]; edits: CodingEdit[]; changes: { kind: string; change: string; code: string }[] }>(`/api/encounters/${id}/check`, json({ icd, cpt })),
  finalizeCodes: (id: number, icd: string[], cpt: CptLine[], reason?: string) =>
    request<EncounterDetail>(`/api/encounters/${id}/finalize`, json({ icd, cpt, reason })),
  reopenEncounter: (id: number) => request<EncounterDetail>(`/api/encounters/${id}/reopen`, { method: "POST" }),
  createClaimFromEncounter: (id: number) => request<EncounterDetail & { claim_number: string }>(`/api/encounters/${id}/claim`, { method: "POST" }),
  retryEncounter: (id: number) => request<EncounterDetail>(`/api/encounters/${id}/retry`, { method: "POST" }),

  opdSummary: () => request<OpdSummary>("/api/frontdesk/summary"),
  opdDoctors: (day?: string) => request<DoctorOption[]>(`/api/frontdesk/doctors${day ? `?day=${day}` : ""}`),
  searchPatients: (q: string) => request<OpdPatient[]>(`/api/frontdesk/patients?q=${encodeURIComponent(q)}`),
  opdPatient: (id: number) => request<OpdPatientDetail>(`/api/frontdesk/patients/${id}`),
  registerPatient: (body: { name: string; sex: string; phone: string; age?: number; dob?: string; abha?: string; city?: string; confirm_new?: boolean }) =>
    request<{ registered: true; patient: OpdPatient } | { registered: false; possible_duplicates: DuplicateMatch[] }>("/api/frontdesk/patients", json(body)),
  opdIntake: (patient_id: number, complaint: string, visit_date?: string) => request<OpdVisit>("/api/frontdesk/intake", json({ patient_id, complaint, visit_date })),
  opdVisit: (id: number) => request<OpdVisit>(`/api/frontdesk/visits/${id}`),
  bookToken: (id: number, doctor_id: number, payment_mode?: string, override_reason?: string) =>
    request<OpdVisit>(`/api/frontdesk/visits/${id}/book`, json({ doctor_id, payment_mode, override_reason })),
  sendToEmergency: (id: number) => request<OpdVisit>(`/api/frontdesk/visits/${id}/emergency`, { method: "POST" }),
  queueAction: (id: number, action: "call" | "done" | "skip" | "recall" | "cancel") => request<OpdVisit>(`/api/frontdesk/visits/${id}/${action}`, { method: "POST" }),
  opdQueue: () => request<{ date: string; doctors: QueueDoctor[] }>("/api/frontdesk/queue"),
  arList: () => request<ArRow[]>("/api/ar"),
  arSummary: () => request<ArSummary>("/api/ar/summary"),
  arDetail: (claimId: number) => request<ArDetail>(`/api/ar/${claimId}`),
  arCheck: (claimId: number) => request<ArDetail>(`/api/ar/${claimId}/check`, { method: "POST" }),
  arAction: (claimId: number, action: string, note?: string) => request<ArDetail>(`/api/ar/${claimId}/action`, json({ action, note })),

  statements: (view: "open" | "all" = "open") => request<StatementRow[]>(`/api/billing/statements?view=${view}`),
  billingSummary: () => request<BillingSummary>("/api/billing/summary"),
  statement: (id: number) => request<StatementRow>(`/api/billing/statements/${id}`),
  recordPayment: (id: number, amount: number, method: string, reference?: string) =>
    request<StatementRow>(`/api/billing/statements/${id}/payments`, json({ amount, method, reference })),
  setUpPlan: (id: number, installments: number) => request<StatementRow>(`/api/billing/statements/${id}/plan`, json({ installments })),
  runReminders: () => request<{ moved: { statement: string; stage: string }[] }>("/api/billing/reminders", { method: "POST" }),

  reconciliation: () => request<Reconciliation>("/api/reconciliation"),
  loadDemoBankStatement: () => request<Reconciliation & { added: number }>("/api/reconciliation/demo-statement", { method: "POST" }),
  importBankStatement: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Reconciliation & { added: number; already_imported: number }>("/api/reconciliation/import", { method: "POST", body: form });
  },
  resolveDeposit: (id: number, note: string) => request<{ ok: boolean }>(`/api/reconciliation/deposits/${id}/resolve`, json({ note })),

  remittanceRaw: async (id: number) => {
    const res = await fetch(`/api/remittances/${id}/raw`, { credentials: "same-origin" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  audit: () => request<AuditEntry[]>("/api/audit"),
  verifyAudit: () => request<{ ok: boolean; entries: number; broken_at: number | null }>("/api/audit/verify"),
};

export const pageImageUrl = (documentId: number, page: number) => `/api/documents/${documentId}/pages/${page}.png`;
