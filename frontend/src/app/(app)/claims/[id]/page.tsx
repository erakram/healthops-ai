"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertOctagon, ArrowLeft, ArrowRight, Ban, CheckCircle2, CircleDollarSign, Clock3, Cpu, FileStack, History, RotateCcw, ShieldAlert, Sparkles } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { ActorDot } from "@/components/actor-dot";
import { AgentConsole } from "@/components/claim/agent-console";
import { DocumentViewer } from "@/components/claim/document-viewer";
import { ExtractedFields } from "@/components/claim/fields";
import { Findings } from "@/components/claim/findings";
import { Lifecycle } from "@/components/claim/lifecycle";
import { ActionChip, CodeBadge, DenialStatusPill } from "@/components/denials/bits";
import { Button, Panel, RiskBadge, Skeleton, StatusPill } from "@/components/ui";
import { api, type ClaimDetail, type Evidence } from "@/lib/api";
import { cn, money, shortDate, timeAgo } from "@/lib/utils";

const PROCESSING = new Set(["received", "extracting", "validating", "approved"]);
const WAITING_ON_PAYER = new Set(["submitted", "appealed"]);

export default function ClaimPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number(use(params).id);
  const { data: claim } = useQuery({
    queryKey: ["claim", id],
    queryFn: () => api.claim(id),
    // Live events refresh this instantly; polling is the fallback while agents work.
    refetchInterval: (q) => (q.state.data && PROCESSING.has(q.state.data.status) ? 1500
      : q.state.data && WAITING_ON_PAYER.has(q.state.data.status) ? 3000 : 30_000),
  });
  if (!claim) return <ClaimSkeleton />;
  return <ClaimView claim={claim} />;
}

function ClaimView({ claim }: { claim: ClaimDetail }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [focus, setFocus] = useState<Evidence | null>(null);
  const [tab, setTab] = useState<"data" | "timeline">("data");
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");

  const running = PROCESSING.has(claim.status);
  const awaiting = claim.status === "needs_review" || claim.status === "ready";
  const canDecide = awaiting && (user?.role === "biller" || user?.role === "admin");
  const canCorrect = awaiting && !!user;
  const update = (data: ClaimDetail) => {
    queryClient.setQueryData(["claim", claim.id], data);
    queryClient.invalidateQueries({ queryKey: ["claims"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard"] });
  };
  const decide = useMutation({
    mutationFn: ({ action, note }: { action: "approve" | "reject"; note?: string }) => api.decide(claim.id, action, note),
    onSuccess: (data, vars) => {
      update(data);
      setRejecting(false);
      toast.success(vars.action === "approve" ? "Approved. Submitting to the payer…" : "Claim rejected");
    },
    onError: (e) => toast.error(e.message),
  });
  const correct = useMutation({
    mutationFn: (v: { documentType: string; field: string; value: string }) => api.correct(claim.id, v.documentType, v.field, v.value),
    onSuccess: (data) => { update(data); toast.success("Saved. Re-validating the claim…"); },
    onError: (e) => toast.error(e.message),
  });
  const retry = useMutation({ mutationFn: () => api.retry(claim.id), onSuccess: update, onError: (e) => toast.error(e.message) });

  const tokens = claim.agent_runs.reduce((s, r) => s + r.input_tokens + r.output_tokens, 0);
  const aiSeconds = claim.agent_runs.reduce((s, r) => s + r.latency_ms, 0) / 1000;
  const serious = claim.findings.filter((f) => f.severity === "critical" || f.severity === "high").length;

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <Link href="/claims" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-white">
          <ArrowLeft className="size-4" /> Claims
        </Link>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="font-mono text-3xl font-semibold tracking-tight text-white">{claim.number}</h1>
              <StatusPill status={claim.status} />
              <RiskBadge risk={claim.risk} />
            </div>
            <p className="mt-2 text-sm text-slate-400">
              {claim.patient_name ?? "Patient not read yet"} · {claim.payer_name ?? "—"} · Service {shortDate(claim.service_date)} ·{" "}
              <span className="font-medium text-slate-200">{money(claim.total_charge)}</span>
            </p>
          </div>

          <AnimatePresence mode="wait">
            {canDecide && !rejecting && (
              <motion.div key="actions" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="flex gap-2">
                <Button variant="danger" onClick={() => setRejecting(true)}><Ban className="size-4" /> Reject</Button>
                <Button variant="primary" loading={decide.isPending} onClick={() => decide.mutate({ action: "approve" })}>
                  <CheckCircle2 className="size-4" /> {serious ? `Approve despite ${serious} issue${serious > 1 ? "s" : ""}` : "Approve & submit"}
                </Button>
              </motion.div>
            )}
            {canDecide && rejecting && (
              <motion.form key="reject" initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                onSubmit={(e) => { e.preventDefault(); decide.mutate({ action: "reject", note }); }} className="flex w-full max-w-lg gap-2">
                <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why is it rejected? (goes in the audit trail)"
                  className="min-w-0 flex-1 rounded-xl border border-rose-400/30 bg-white/[0.03] px-3 text-sm text-white outline-none focus:ring-4 focus:ring-rose-400/10" />
                <Button type="button" variant="ghost" onClick={() => setRejecting(false)}>Cancel</Button>
                <Button type="submit" variant="danger" disabled={!note.trim()} loading={decide.isPending}>Reject</Button>
              </motion.form>
            )}
            {claim.status === "failed" && (
              <Button key="retry" variant="secondary" loading={retry.isPending} onClick={() => retry.mutate()}><RotateCcw className="size-4" /> Retry</Button>
            )}
          </AnimatePresence>
        </div>
      </div>

      <Panel glow={false} className="p-5"><Lifecycle status={claim.status} /></Panel>

      {claim.status === "submitted" && !claim.remittances.length && (
        <div className="flex items-center gap-3 rounded-2xl border border-sky-400/20 bg-sky-500/[0.06] p-4 text-sm text-sky-100">
          <span className="relative flex size-2.5"><span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-sky-400" /><span className="relative size-2.5 rounded-full bg-sky-400" /></span>
          With {claim.payer_name ?? "the payer"}. Their 835 remittance will arrive here.
        </div>
      )}
      {claim.financials && <Payment claim={claim} />}

      {/* Agent console while processing (and briefly after, for context) */}
      <AnimatePresence>
        {(running || claim.status === "failed" || claim.agent_runs.length > 0) && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }}>
            <AgentConsole events={claim.events} running={running} />
          </motion.div>
        )}
      </AnimatePresence>

      <div className="grid gap-5 xl:grid-cols-[1fr_minmax(0,560px)]">
        <div className="space-y-5">
          {/* Findings */}
          <Panel className="p-6">
            <div className="mb-4 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <ShieldAlert className="size-4 text-orange-300" />
                <h2 className="font-medium text-white">Findings</h2>
                {!!claim.findings.length && <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-xs text-slate-300">{claim.findings.length}</span>}
              </div>
              {claim.findings.some((f) => f.evidence.length) && <p className="text-xs text-slate-500">Click evidence to see it on the page</p>}
            </div>
            {claim.status === "failed" ? (
              <div className="flex items-start gap-4 rounded-2xl border border-rose-400/25 bg-gradient-to-r from-rose-500/10 to-transparent p-5">
                <div className="grid size-11 shrink-0 place-items-center rounded-xl bg-rose-500/15 ring-1 ring-rose-400/30"><AlertOctagon className="size-6 text-rose-300" /></div>
                <div className="min-w-0">
                  <p className="font-medium text-rose-100">Processing stopped before the checks ran</p>
                  <p className="mt-1 break-words text-sm text-rose-200/60">
                    {[...claim.events].reverse().find((e) => e.type === "transition" && e.data?.to === "failed")?.message ?? "An agent step failed."}
                  </p>
                  <p className="mt-2 text-xs text-slate-400">Nothing was decided or submitted. Retry once the cause is fixed.</p>
                </div>
              </div>
            ) : running && !claim.findings.length ? (
              <div className="space-y-2">
                {[0, 1, 2].map((i) => <Skeleton key={i} className="h-20 rounded-xl" />)}
              </div>
            ) : (
              <Findings findings={claim.findings} onFocus={setFocus} focused={focus} />
            )}
          </Panel>

          {/* Data / timeline */}
          <Panel className="p-6">
            <div className="mb-5 flex items-center justify-between">
              <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
                {([["data", "Extracted data", FileStack], ["timeline", "Timeline", History]] as const).map(([key, label, Icon]) => (
                  <button key={key} onClick={() => setTab(key)}
                    className={cn("relative flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm", tab === key ? "text-white" : "text-slate-400 hover:text-slate-200")}>
                    {tab === key && <motion.span layoutId="detail-tab" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                    <Icon className="relative size-3.5" /><span className="relative">{label}</span>
                  </button>
                ))}
              </div>
              {canCorrect && tab === "data" && <p className="hidden text-xs text-slate-500 md:block">Wrong value? Correct it and the claim re-validates.</p>}
            </div>
            {tab === "data" ? (
              running && !Object.keys(claim.extracted).length ? (
                <div className="grid gap-2 sm:grid-cols-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-20 rounded-xl" />)}</div>
              ) : (
                <ExtractedFields extracted={claim.extracted} schema={claim.schema} editable={canCorrect} saving={correct.isPending}
                  onFocus={setFocus} onCorrect={(documentType, field, value) => correct.mutate({ documentType, field, value })} />
              )
            ) : (
              <Timeline claim={claim} />
            )}
          </Panel>

          {/* AI usage */}
          {claim.agent_runs.length > 0 && (
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                { icon: Sparkles, label: "Agent calls", value: String(claim.agent_runs.length) },
                { icon: Cpu, label: "Tokens", value: tokens.toLocaleString() },
                { icon: Clock3, label: "Model time", value: `${aiSeconds.toFixed(1)}s` },
                { icon: CheckCircle2, label: "Upload to review", value: claim.processed_seconds ? `${claim.processed_seconds}s` : "—" },
              ].map((s, i) => (
                <motion.div key={s.label} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }}
                  className="panel p-4">
                  <s.icon className="mb-2 size-4 text-cyan-300" />
                  <p className="text-lg font-semibold tabular-nums text-white">{s.value}</p>
                  <p className="text-xs text-slate-500">{s.label}</p>
                </motion.div>
              ))}
            </div>
          )}
        </div>

        {/* Documents */}
        <div className="xl:sticky xl:top-6 xl:self-start">
          <Panel glow={false} className="h-[78vh] min-h-[520px] p-0 [&>div:last-child]:h-full">
            {claim.documents.length ? (
              <DocumentViewer documents={claim.documents} focus={focus} onSelect={() => setFocus(null)} />
            ) : (
              <div className="grid h-full place-items-center text-sm text-slate-500">No documents</div>
            )}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function Payment({ claim }: { claim: ClaimDetail }) {
  const f = claim.financials!;
  const billed = f.billed || 1;
  const parts = [
    { label: "Paid by payer", value: f.paid, bar: "bg-gradient-to-r from-emerald-400 to-teal-300", text: "text-emerald-300" },
    { label: "Patient owes", value: f.patient_responsibility, bar: "bg-amber-300", text: "text-amber-200" },
    { label: "Written off", value: f.written_off, bar: "bg-slate-500", text: "text-slate-300" },
  ];
  return (
    <Panel className="p-6">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2"><CircleDollarSign className="size-4 text-emerald-300" /><h2 className="font-medium text-white">Payment</h2>
          <span className="text-xs text-slate-500">· from {claim.remittances.length} remittance{claim.remittances.length === 1 ? "" : "s"} (835)</span></div>
        {f.recovered > 0 && <span className="rounded-lg bg-emerald-400/10 px-2 py-1 text-xs text-emerald-200 ring-1 ring-emerald-400/25">+{money(f.recovered)} recovered by the denial agents</span>}
      </div>
      <div className="grid gap-4 sm:grid-cols-4">
        <div><p className="text-xs text-slate-500">Billed</p><p className="text-xl font-semibold tabular-nums text-white">{money(f.billed)}</p></div>
        <div><p className="text-xs text-slate-500">Allowed under contract</p><p className="text-xl font-semibold tabular-nums text-slate-200">{money(f.allowed)}</p></div>
        {parts.slice(0, 2).map((p) => <div key={p.label}><p className="text-xs text-slate-500">{p.label}</p><p className={cn("text-xl font-semibold tabular-nums", p.text)}>{money(p.value)}</p></div>)}
      </div>
      <div className="mt-4 flex h-2.5 overflow-hidden rounded-full bg-white/[0.05]">
        {parts.map((p, i) => (
          <motion.div key={p.label} title={`${p.label}: ${money(p.value)}`} className={cn("h-full", p.bar)}
            initial={{ width: 0 }} animate={{ width: `${Math.max(0, (p.value / billed) * 100)}%` }} transition={{ delay: 0.2 + i * 0.15, duration: 0.9, ease: [0.16, 1, 0.3, 1] }} />
        ))}
      </div>
      <p className="mt-1.5 text-[11px] text-slate-500">The rest of the billed amount is the contractual adjustment (CO-45) or still in dispute.</p>
      {claim.denials.length > 0 && (
        <div className="mt-4 space-y-1.5">
          {claim.denials.map((d) => (
            <Link key={d.id} href={`/denials/${d.id}`} className="group flex items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 transition hover:border-white/15">
              <CodeBadge code={d.code} />
              <div className="min-w-0 flex-1"><p className="truncate text-sm text-slate-200">{d.reason}</p><p className="text-xs text-slate-500">{money(d.amount)} · CPT {d.lines.map((l) => l.cpt).join(", ")}</p></div>
              {d.status === "open" ? <ActionChip action={d.recommended_action} /> : <DenialStatusPill status={d.status} label={d.status_label} />}
              <ArrowRight className="size-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-slate-300" />
            </Link>
          ))}
        </div>
      )}
    </Panel>
  );
}

function Timeline({ claim }: { claim: ClaimDetail }) {
  const events = [...claim.events].reverse();
  return (
    <ol className="relative space-y-4 before:absolute before:left-[13px] before:top-2 before:bottom-2 before:w-px before:bg-white/[0.07]">
      {events.map((e, i) => (
        <motion.li key={e.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: Math.min(i, 12) * 0.03 }}
          className="relative flex gap-3">
          <ActorDot actor={e.actor} />
          <div className="min-w-0 flex-1">
            <p className={cn("text-sm", e.type === "decision" ? "font-medium text-white" : "text-slate-200")}>{e.message}</p>
            <p className="text-xs text-slate-500">{e.actor} · {timeAgo(e.at)}</p>
            {typeof e.data?.note === "string" && e.data.note && <p className="mt-1 rounded-lg bg-white/[0.03] px-2 py-1 text-xs italic text-slate-400">“{e.data.note}”</p>}
          </div>
        </motion.li>
      ))}
      {claim.submission && (
        <li className="relative flex gap-3 pl-10 text-xs text-emerald-300">Payer reference {claim.submission.payer_reference}</li>
      )}
    </ol>
  );
}

function ClaimSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-10 w-72" />
      <Skeleton className="h-24 rounded-2xl" />
      <div className="grid gap-5 xl:grid-cols-[1fr_560px]">
        <Skeleton className="h-[500px] rounded-2xl" />
        <Skeleton className="h-[500px] rounded-2xl" />
      </div>
    </div>
  );
}
