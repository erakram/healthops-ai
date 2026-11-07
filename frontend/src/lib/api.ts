// Typed client for the FastAPI backend. Requests go to /api/*, which Next.js rewrites to
// the backend, so the session cookie is same-origin.

export type Role = "intake" | "biller" | "admin";
export type ClaimStatus =
  | "received" | "extracting" | "validating" | "needs_review" | "ready"
  | "approved" | "rejected" | "submitted" | "failed";
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
  schema: FieldSchema;
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

export interface SampleCase { case_id: string; title: string; scenario: string; patient: string; files: string[] }

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
  audit: () => request<AuditEntry[]>("/api/audit"),
  verifyAudit: () => request<{ ok: boolean; entries: number; broken_at: number | null }>("/api/audit/verify"),
};

export const pageImageUrl = (documentId: number, page: number) => `/api/documents/${documentId}/pages/${page}.png`;
