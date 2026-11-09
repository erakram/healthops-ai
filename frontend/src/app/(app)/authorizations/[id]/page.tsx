"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon, ArrowLeft, BadgeCheck, Bot, Braces, Check, CheckCircle2, ChevronDown, CircleDashed, ClipboardCheck,
  Cpu, FilePlus2, FileSearch, Inbox, RotateCcw, Scale, Send, ShieldCheck, Sparkles, Undo2, UploadCloud, UserCheck, X, XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { use, useRef, useState } from "react";
import { toast } from "sonner";
import { AuthStatusPill, PayerClock, UrgentBadge } from "@/components/auth/bits";
import { AgentConsole } from "@/components/claim/agent-console";
import { DocumentViewer } from "@/components/claim/document-viewer";
import { AppealEditor, AppealWriting } from "@/components/denials/appeal-editor";
import { PolicyCard } from "@/components/denials/policy-card";
import { Button, Panel, Skeleton } from "@/components/ui";
import { api, type Appeal, type AuthDetail, type AuthStatus, type Evidence } from "@/lib/api";
import { cn, shortDate } from "@/lib/utils";

const WORKING = new Set<AuthStatus>(["received", "reading", "checking", "submitted"]);
const SUBMITTABLE = new Set<AuthStatus>(["needs_documentation", "ready", "pended"]);
const CAN_ADD = new Set<AuthStatus>(["needs_documentation", "ready", "pended", "denied", "ineligible"]);

export default function AuthorizationPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number(use(params).id);
  const { data } = useQuery({
    queryKey: ["auth", id], queryFn: () => api.authorization(id),
    refetchInterval: (q) => (q.state.data && WORKING.has(q.state.data.status) ? 1500 : 20_000),
  });
  if (!data) return <Loading />;
  return <AuthView a={data} />;
}

function AuthView({ a }: { a: AuthDetail }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [focus, setFocus] = useState<Evidence | null>(null);
  const [policyQuote, setPolicyQuote] = useState<string | null>(null);
  const [pending, setPending] = useState<{ source_index: number | null; text: string }[] | null>(null);
  const [note, setNote] = useState("");
  const [withdrawing, setWithdrawing] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  const canAct = !!user && ["intake", "biller", "admin"].includes(user.role);

  const refresh = (data: AuthDetail) => {
    queryClient.setQueryData(["auth", a.id], data);
    queryClient.invalidateQueries({ queryKey: ["auths"] });
  };
  const onError = (e: Error) => toast.error(e.message);
  const addSample = useMutation({ mutationFn: () => api.addSampleAddendum(a.id), onSuccess: (d) => { refresh(d); toast.success("Addendum added. Re-checking the criteria…"); }, onError });
  const addFile = useMutation({ mutationFn: (f: File) => api.addAuthDocument(a.id, f), onSuccess: (d) => { refresh(d); toast.success("Documentation added. Re-checking…"); }, onError });
  const save = useMutation({ mutationFn: (p: { source_index: number | null; text: string }[]) => api.saveAuthLetter(a.id, p), onSuccess: (d) => { refresh(d); toast.success("Draft saved"); }, onError });
  const submit = useMutation({
    mutationFn: (v: { p: { source_index: number | null; text: string }[]; note?: string }) => api.submitAuthorization(a.id, v.p, v.note),
    onSuccess: (d) => { refresh(d); setPending(null); setNote(""); toast.success("Sent to the payer as a FHIR PAS request"); }, onError,
  });
  const withdraw = useMutation({ mutationFn: (n: string) => api.withdrawAuthorization(a.id, n), onSuccess: (d) => { refresh(d); setWithdrawing(false); }, onError });
  const retry = useMutation({ mutationFn: () => api.retryAuthorization(a.id), onSuccess: refresh, onError });

  const criteria = a.assessment.criteria ?? [];
  const missing = a.assessment.missing ?? [];
  const tokens = a.agent_runs.reduce((s, r) => s + r.input_tokens + r.output_tokens, 0);
  const draftAvailable = !!a.draft.paragraphs?.length;
  const letterAppeal: Appeal = {
    id: a.id, status: SUBMITTABLE.has(a.status) ? "draft" : a.status === "submitted" ? "sent" : "upheld",
    draft: a.draft, letter: a.letter, edited_by: null, sent_by: null, sent_at: a.submitted_at, decided_at: a.decided_at,
    outcome_reason: null,
  };
  const onSend = (p: { source_index: number | null; text: string }[]) =>
    a.status === "needs_documentation" ? setPending(p) : submit.mutate({ p });

  return (
    <div className="space-y-5">
      <div>
        <Link href="/authorizations" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-white">
          <ArrowLeft className="size-4" /> Authorizations
        </Link>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="font-mono text-3xl font-semibold tracking-tight text-white">{a.number}</h1>
              <AuthStatusPill status={a.status} label={a.status_label} />
              {a.urgency === "urgent" && <UrgentBadge />}
            </div>
            <p className="mt-2 text-sm text-slate-400">
              {a.patient_name ?? "Patient not read yet"} · {a.payer_name ?? "—"} · scheduled {shortDate(a.scheduled_date)}
              {a.services.length > 0 && <> · <span className="text-slate-200">{a.services.map((s) => `CPT ${s.cpt} ${s.description ?? ""}`).join(", ")}</span></>}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {a.status === "submitted" && <PayerClock hours={a.hours_left} urgent={a.urgency === "urgent"} />}
            {a.status === "failed" && canAct && <Button loading={retry.isPending} onClick={() => retry.mutate()}><RotateCcw className="size-4" /> Retry</Button>}
            {canAct && ["needs_documentation", "ready", "pended", "denied", "ineligible"].includes(a.status) && !withdrawing && (
              <Button variant="ghost" onClick={() => setWithdrawing(true)}><Undo2 className="size-4" /> Withdraw</Button>
            )}
            {withdrawing && (
              <form onSubmit={(e) => { e.preventDefault(); withdraw.mutate(note); }} className="flex gap-2">
                <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why? (goes in the audit trail)"
                  className="w-64 rounded-xl border border-white/15 bg-white/[0.03] px-3 text-sm text-white outline-none" />
                <Button type="button" variant="ghost" onClick={() => setWithdrawing(false)}>Cancel</Button>
                <Button type="submit" variant="danger" disabled={!note.trim()} loading={withdraw.isPending}>Withdraw</Button>
              </form>
            )}
          </div>
        </div>
      </div>

      <Panel glow={false} className="p-5"><AuthLifecycle a={a} /></Panel>

      <Decision a={a} onFocusLetter={(id) => setFocus({ document_id: id, page: 1, bbox: [0, 0, 0, 0], text: "" })} />

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <div className="min-w-0 space-y-5">
          {/* Requirement */}
          {a.requirement.reasons && (
            <Panel className="p-6">
              <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-teal-300/80"><ShieldCheck className="size-4" /> Requirement check · rules, no AI</div>
              <ul className="space-y-2">
                {a.requirement.reasons.map((r, i) => (
                  <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }} className="flex gap-2.5 text-sm text-slate-200">
                    {a.requirement.eligible === false && i === 0 ? <XCircle className="mt-0.5 size-4 shrink-0 text-rose-300" /> : <Check className="mt-0.5 size-4 shrink-0 text-teal-300" />}{r}
                  </motion.li>
                ))}
              </ul>
            </Panel>
          )}

          {/* Criteria */}
          {(criteria.length > 0 || a.status === "checking") && (
            <Panel className="p-6">
              <div className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-gradient-to-br from-violet-500/15 to-transparent blur-3xl" />
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-violet-300/80"><Bot className="size-4" /> Clinical Criteria Agent{a.assessment.policy_id ? ` · ${a.assessment.policy_id}` : ""}</div>
                {a.action_label && <span className={cn("rounded-lg px-2 py-1 text-xs font-medium ring-1 ring-inset",
                  a.action === "submit" ? "bg-emerald-400/10 text-emerald-200 ring-emerald-400/25" : a.action === "do_not_submit" ? "bg-rose-400/10 text-rose-200 ring-rose-400/25" : "bg-amber-400/10 text-amber-200 ring-amber-400/30")}>{a.action_label}</span>}
              </div>
              {a.status === "checking" && !criteria.length ? (
                <div className="space-y-2"><Skeleton className="h-14 rounded-xl" /><Skeleton className="h-14 rounded-xl" /></div>
              ) : (
                <>
                  <ul className="mb-4 space-y-1.5">{(a.assessment.reasons ?? []).map((r, i) => <li key={i} className="flex gap-2.5 text-sm text-slate-200"><span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-violet-400" />{r}</li>)}</ul>
                  <ul className="space-y-2.5">
                    {criteria.map((c, i) => (
                      <motion.li key={c.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.08 }}
                        className={cn("rounded-xl border p-3", c.met ? "border-emerald-400/20 bg-emerald-400/[0.04]" : "border-amber-400/25 bg-amber-400/[0.05]")}>
                        <div className="flex gap-2.5">
                          {c.met ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-300" /> : <CircleDashed className="mt-0.5 size-4 shrink-0 text-amber-300" />}
                          <div className="min-w-0">
                            <p className="text-sm text-slate-100"><span className="font-mono text-xs text-slate-500">{c.id}</span> {c.text}</p>
                            {c.met && c.quote && (
                              <button onClick={() => c.evidence && setFocus(c.evidence)}
                                className="mt-1.5 block max-w-full truncate rounded-md bg-teal-400/10 px-2 py-1 text-left text-xs text-teal-100 ring-1 ring-teal-400/20 hover:bg-teal-400/20">
                                {c.source && c.source !== "clinical_note" && <span className="mr-1 font-semibold text-amber-200">Addendum:</span>}“{c.quote}”
                              </button>
                            )}
                            {!c.met && c.model_said_met && <p className="mt-1 text-xs text-amber-200/80">The model said met, but its quote isn&apos;t in the notes, so it doesn&apos;t count.</p>}
                            {!c.met && !c.model_said_met && c.explanation && <p className="mt-1 text-xs text-slate-400">{c.explanation}</p>}
                          </div>
                        </div>
                      </motion.li>
                    ))}
                  </ul>
                </>
              )}
            </Panel>
          )}

          {/* Missing documentation */}
          {missing.length > 0 && CAN_ADD.has(a.status) && (
            <Panel className="border-amber-400/20 p-6">
              <div className="mb-3 flex items-center gap-2"><FilePlus2 className="size-4 text-amber-300" /><h2 className="font-medium text-white">Ask the physician</h2>
                <span className="text-xs text-slate-500">· fix it before the payer has to ask</span></div>
              <ul className="space-y-2">
                {missing.map((m) => (
                  <li key={m.id} className="flex gap-3 rounded-xl bg-amber-400/[0.06] p-3 text-sm">
                    <span className="font-mono text-xs text-amber-300">{m.id}</span>
                    <span className="text-amber-50">{m.ask}</span>
                  </li>
                ))}
              </ul>
              {canAct && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {a.has_sample_addendum && (
                    <Button variant="primary" loading={addSample.isPending} onClick={() => addSample.mutate()}>
                      <Sparkles className="size-4" /> Use the physician&apos;s sample addendum
                    </Button>
                  )}
                  <Button loading={addFile.isPending} onClick={() => file.current?.click()}><UploadCloud className="size-4" /> Upload an addendum (PDF)</Button>
                  <input ref={file} type="file" accept="application/pdf" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) addFile.mutate(f); e.target.value = ""; }} />
                </div>
              )}
            </Panel>
          )}

          {/* Request letter */}
          {a.status === "checking" && criteria.length > 0 && !draftAvailable && <AppealWriting />}
          {(draftAvailable || a.draft.historical) && (
            <Panel glow={false} className="p-6">
              <div className="mb-4 flex items-center gap-2"><Scale className="size-4 text-violet-300" /><h2 className="font-medium text-white">Medical necessity summary</h2>
                <span className="text-xs text-slate-500">· drafted by the Request Agent, sent by a person as a FHIR PAS request</span></div>
              {a.draft.historical ? <p className="text-sm text-slate-400">Historical demo record: the request text wasn&apos;t kept.</p> : (
                <AppealEditor appeal={letterAppeal} editable={canAct && SUBMITTABLE.has(a.status)} sending={submit.isPending} saving={save.isPending}
                  recovered={0} onSend={onSend} onSave={(p) => save.mutate(p)} onFocus={setFocus} onPolicy={setPolicyQuote}
                  sendLabel={a.status === "pended" ? "Resubmit to payer" : "Submit to payer"} waitingLabel="Sent. Waiting for the payer's utilization review…" />
              )}
              <AnimatePresence>
                {pending && (
                  <motion.form initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                    onSubmit={(e) => { e.preventDefault(); submit.mutate({ p: pending, note }); }}
                    className="mt-4 rounded-2xl border border-amber-400/30 bg-amber-400/[0.06] p-4">
                    <p className="text-sm text-amber-50">Documentation is still missing for {missing.map((m) => m.id).join(", ")}. The payer will likely pend this request. Say why you&apos;re sending it anyway:</p>
                    <div className="mt-3 flex gap-2">
                      <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="e.g. surgeon requested submission; peer-to-peer planned"
                        className="min-w-0 flex-1 rounded-xl border border-amber-400/30 bg-black/20 px-3 text-sm text-white outline-none" />
                      <Button type="button" variant="ghost" onClick={() => setPending(null)}><X className="size-4" /></Button>
                      <Button type="submit" variant="danger" disabled={!note.trim()} loading={submit.isPending}><Send className="size-4" /> Submit anyway</Button>
                    </div>
                  </motion.form>
                )}
              </AnimatePresence>
            </Panel>
          )}

          {a.fhir_request?.resourceType ? <FhirPanel a={a} /> : null}
          <AgentConsole events={a.events} running={WORKING.has(a.status)} />
          {tokens > 0 && <p className="flex items-center gap-1.5 text-xs text-slate-500"><Cpu className="size-3.5" /> {a.agent_runs.length} agent calls · {tokens.toLocaleString()} tokens</p>}
        </div>

        <div className="space-y-5 xl:sticky xl:top-6 xl:self-start">
          <Panel glow={false} className="h-[64vh] min-h-[460px] p-0 [&>div:last-child]:h-full">
            {a.documents.length ? <DocumentViewer documents={a.documents.map((d) => ({ ...d, label: d.addendum ? "Addendum" : d.label }))} focus={focus} onSelect={() => setFocus(null)} />
              : <div className="grid h-full place-items-center text-sm text-slate-500">No documents</div>}
          </Panel>
          {a.policy && <PolicyCard policy={a.policy} quote={policyQuote} criteriaMet={criteria} />}
        </div>
      </div>
    </div>
  );
}

const STEPS = [
  { label: "Received", icon: Inbox }, { label: "Reading", icon: FileSearch }, { label: "Requirement", icon: ShieldCheck },
  { label: "Criteria", icon: Bot }, { label: "Staff review", icon: UserCheck }, { label: "With payer", icon: Send },
  { label: "Decision", icon: ClipboardCheck },
];
const POSITION: Record<AuthStatus, number> = {
  received: 0, reading: 1, checking: 3, not_required: 2, ineligible: 2, needs_documentation: 4, ready: 4,
  submitted: 5, approved: 6, pended: 6, denied: 6, withdrawn: 4, failed: 1,
};

function AuthLifecycle({ a }: { a: AuthDetail }) {
  const current = POSITION[a.status];
  const ended = ["approved", "not_required"].includes(a.status);
  const bad = ["denied", "ineligible", "failed", "withdrawn"].includes(a.status);
  const warn = ["needs_documentation", "pended"].includes(a.status);
  const finalLabel: Partial<Record<AuthStatus, string>> = {
    approved: "Approved", pended: "More info", denied: "Denied", not_required: "Not needed", ineligible: "Inactive",
    needs_documentation: "Gap found", withdrawn: "Withdrawn", failed: "Failed",
  };
  return (
    <div className="relative px-1 py-2">
      <div className="absolute left-[7.14%] right-[7.14%] top-[26px] h-[2px] rounded-full bg-white/[0.06]">
        <motion.div className={cn("h-full rounded-full", bad ? "bg-rose-400/70" : warn ? "bg-gradient-to-r from-teal-400 to-amber-300" : "bg-gradient-to-r from-teal-400 via-cyan-400 to-violet-400")}
          initial={{ width: 0 }} animate={{ width: `${(current / (STEPS.length - 1)) * 100}%` }} transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }} />
      </div>
      <div className="relative grid grid-cols-7">
        {STEPS.map((step, i) => {
          const here = i === current;
          const done = i < current || (ended && here);
          const tone = here && bad ? "bg-rose-500/20 text-rose-200 ring-rose-400/40" : here && warn ? "bg-amber-400/20 text-amber-100 ring-amber-300/50"
            : done ? "bg-gradient-to-br from-teal-400 to-cyan-500 text-ink-950 ring-transparent shadow-[0_0_20px_-4px_rgba(45,212,191,0.8)]"
            : here ? "bg-ink-800 text-white ring-teal-300/60" : "bg-ink-900 text-slate-500 ring-white/10";
          const Icon = here && bad ? X : here && warn ? AlertOctagon : done ? Check : step.icon;
          return (
            <div key={step.label} className="flex flex-col items-center gap-2">
              <motion.div initial={{ scale: 0.6, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: i * 0.07, type: "spring", stiffness: 300, damping: 20 }}
                className={cn("relative grid size-[38px] place-items-center rounded-xl ring-1", tone)}>
                {here && !ended && !bad && <span className="absolute inset-0 animate-pulse-ring rounded-xl ring-2 ring-teal-300/50" />}
                <Icon className="size-[17px]" strokeWidth={done ? 3 : 2} />
              </motion.div>
              <span className={cn("text-center text-[11px] font-medium", here || done ? "text-slate-200" : "text-slate-500")}>
                {here && finalLabel[a.status] ? finalLabel[a.status] : step.label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Decision({ a, onFocusLetter }: { a: AuthDetail; onFocusLetter: (documentId: number) => void }) {
  const d = a.decision;
  if (!d.decision || a.status === "submitted") return null;
  const letter = a.documents.find((doc) => doc.kind === "prior_authorization");
  const tone = d.decision === "approved" ? "border-emerald-300/30 from-emerald-500/15 via-teal-500/10" : d.decision === "pended" ? "border-orange-400/30 from-orange-500/12" : "border-rose-400/25 from-rose-500/12";
  const Icon = d.decision === "approved" ? BadgeCheck : d.decision === "pended" ? AlertOctagon : XCircle;
  return (
    <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", damping: 20 }}
      className={cn("relative overflow-hidden rounded-2xl border bg-gradient-to-r to-transparent p-5", tone)}>
      {d.decision === "approved" && <motion.div className="pointer-events-none absolute -left-10 -top-10 size-40 rounded-full bg-emerald-400/30 blur-3xl"
        animate={{ scale: [1, 1.3, 1], opacity: [0.6, 0.9, 0.6] }} transition={{ duration: 3, repeat: Infinity }} />}
      <div className="relative flex flex-wrap items-start gap-4">
        <div className={cn("grid size-11 shrink-0 place-items-center rounded-xl ring-1", d.decision === "approved" ? "bg-emerald-400/20 text-emerald-200 ring-emerald-300/40" : d.decision === "pended" ? "bg-orange-400/15 text-orange-200 ring-orange-400/30" : "bg-rose-500/15 text-rose-200 ring-rose-400/30")}><Icon className="size-5" /></div>
        <div className="min-w-0 flex-1">
          <p className="text-xs uppercase tracking-wider text-slate-400">Payer decision · FHIR review action {d.review_action}{a.attempts > 1 ? ` · attempt ${a.attempts}` : ""}</p>
          {d.decision === "approved" ? (
            <p className="mt-1 text-lg font-semibold text-emerald-50">Approved · <span className="font-mono">{d.auth_number}</span>
              <span className="ml-2 text-sm font-normal text-emerald-100/70">valid {shortDate(d.valid_from)} to {shortDate(d.valid_to)}</span></p>
          ) : <p className="mt-1 font-semibold text-white">{d.decision === "pended" ? "Pended: the payer needs more information" : "Denied"}</p>}
          <p className="mt-1 text-sm text-slate-300">{d.disposition}</p>
          {!!d.notes?.length && <ul className="mt-2 space-y-1">{d.notes.map((n, i) => <li key={i} className="text-sm text-orange-100/90">• {n}</li>)}</ul>}
          {a.turnaround_seconds != null && <p className="mt-2 text-xs text-slate-500">Answered {a.turnaround_seconds < 3600 ? `${a.turnaround_seconds.toFixed(0)}s` : `${(a.turnaround_seconds / 3600).toFixed(1)}h`} after submission</p>}
        </div>
        {letter && <Button onClick={() => onFocusLetter(letter.id)}><FileSearch className="size-4" /> Determination letter</Button>}
      </div>
    </motion.div>
  );
}

function FhirPanel({ a }: { a: AuthDetail }) {
  const [tab, setTab] = useState<"request" | "response">(a.fhir_response?.resourceType ? "response" : "request");
  const [open, setOpen] = useState(false);
  const data = tab === "request" ? a.fhir_request : a.fhir_response;
  const text = JSON.stringify(data, (k, v) => (k === "data" && typeof v === "string" && v.length > 80 ? `${v.slice(0, 60)}… (base64)` : v), 2);
  return (
    <Panel glow={false} className="p-6">
      <button onClick={() => setOpen(!open)} className="flex w-full items-center gap-2 text-left">
        <Braces className="size-4 text-sky-300" /><h2 className="font-medium text-white">FHIR exchange</h2>
        <span className="text-xs text-slate-500">· Da Vinci PAS-shaped Bundle: Claim (use: preauthorization) → ClaimResponse</span>
        <ChevronDown className={cn("ml-auto size-4 text-slate-400 transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence>
        {open && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
            <div className="mt-4 flex gap-1">
              {(["request", "response"] as const).map((t) => (
                <button key={t} disabled={t === "response" && !a.fhir_response?.resourceType} onClick={() => setTab(t)}
                  className={cn("rounded-lg px-3 py-1 text-xs", tab === t ? "bg-white/[0.08] text-white ring-1 ring-white/10" : "text-slate-400 hover:text-white disabled:opacity-40")}>
                  {t === "request" ? "Request Bundle" : "Response Bundle"}
                </button>
              ))}
            </div>
            <pre className="mt-3 max-h-96 overflow-auto rounded-xl border border-white/[0.06] bg-ink-950/80 p-3 font-mono text-[11.5px] leading-relaxed">
              {text.split("\n").map((line, i) => {
                const m = line.match(/^(\s*)"([^"]+)":(.*)$/);
                return (
                  <div key={i}>
                    {m ? <>{m[1]}<span className="text-sky-300">&quot;{m[2]}&quot;</span>:<span className={cn(/"(preauthorization|A1|A3|A4|PA-\d+)"/.test(m[3]) ? "font-semibold text-emerald-300" : "text-slate-300")}>{m[3]}</span></> : <span className="text-slate-500">{line}</span>}
                  </div>
                );
              })}
            </pre>
          </motion.div>
        )}
      </AnimatePresence>
    </Panel>
  );
}

function Loading() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-24 rounded-2xl" />
      <div className="grid gap-5 xl:grid-cols-[1fr_520px]"><Skeleton className="h-[480px] rounded-2xl" /><Skeleton className="h-[480px] rounded-2xl" /></div>
    </div>
  );
}
