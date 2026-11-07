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

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
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
  remittanceRaw: async (id: number) => {
    const res = await fetch(`/api/remittances/${id}/raw`, { credentials: "same-origin" });
    if (!res.ok) throw new ApiError(res.status, res.statusText);
    return res.text();
  },
  audit: () => request<AuditEntry[]>("/api/audit"),
  verifyAudit: () => request<{ ok: boolean; entries: number; broken_at: number | null }>("/api/audit/verify"),
};

export const pageImageUrl = (documentId: number, page: number) => `/api/documents/${documentId}/pages/${page}.png`;
