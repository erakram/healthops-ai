"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlarmClock, Binary, Bot, ChevronDown, Hourglass, Radar, Send, Sparkles, Timer } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useState } from "react";
import { toast } from "sonner";
import { StatCard } from "@/components/stat-card";
import { Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type ArRow } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const BUCKET_TONE: Record<string, string> = {
  "0-30": "bg-teal-400", "31-60": "bg-amber-300", "61-90": "bg-orange-400", "90+": "bg-rose-500",
};
const STATUS_TONE: Record<string, string> = {
  D0: "text-rose-200 bg-rose-500/15", P3: "text-amber-200 bg-amber-400/15", F1: "text-emerald-200 bg-emerald-400/15",
  F2: "text-rose-200 bg-rose-500/15", P1: "text-sky-200 bg-sky-400/15", A2: "text-sky-200 bg-sky-400/15", A7: "text-rose-200 bg-rose-500/15",
};

export default function ReceivablesPage() {
  const { data: summary } = useQuery({ queryKey: ["ar", "summary"], queryFn: api.arSummary, refetchInterval: 15_000 });
  const { data: rows } = useQuery({ queryKey: ["ar", "list"], queryFn: api.arList, refetchInterval: 15_000 });
  const max = Math.max(...(summary?.buckets ?? []).map((b) => b.amount), 1);
  return (
    <>
      <PageHeader eyebrow="Unpaid claims" title="Accounts receivable"
        description="Claims the payer hasn't answered. A status check sends an X12 276 and reads the payer's 277; the A/R Follow-up Agent says what the status means, recommends the next step, and drafts the message to the payer." />
      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Hourglass} tone="sky" label="Waiting on payers" value={summary?.amount ?? 0} format={(v) => money(v, true)} foot={`${summary?.claims ?? 0} claims with no 835`} />
        <StatCard icon={AlarmClock} tone="amber" label="Due for follow-up" value={summary?.due_for_follow_up ?? 0} foot="14+ days with the payer" />
        <StatCard icon={Timer} tone="rose" label="Filing deadline in 30 days" value={summary?.filing_risk ?? 0} foot="lose them and the money is gone" />
        <StatCard icon={Radar} tone="violet" label="Average age" value={summary?.average_age_days ?? 0}
          format={(v) => (summary?.average_age_days == null ? "—" : `${v.toFixed(0)} days`)} foot="dollar-weighted" />
      </motion.div>

      <Panel className="mb-5 p-6">
        <p className="mb-4 text-sm font-medium text-white">Aging</p>
        <div className="grid grid-cols-4 gap-4">
          {(summary?.buckets ?? []).map((b, i) => (
            <div key={b.bucket}>
              <div className="flex h-32 items-end rounded-xl bg-white/[0.02] p-2">
                <motion.div initial={{ height: 0 }} animate={{ height: `${Math.max((b.amount / max) * 100, b.count ? 6 : 0)}%` }}
                  transition={{ delay: 0.2 + i * 0.1, duration: 0.8, ease: [0.16, 1, 0.3, 1] }} className={cn("w-full rounded-lg", BUCKET_TONE[b.bucket])} />
              </div>
              <p className="mt-2 text-sm font-medium text-white">{b.bucket} days</p>
              <p className="text-xs text-slate-400">{money(b.amount)} · {b.count} claim{b.count === 1 ? "" : "s"}</p>
            </div>
          ))}
        </div>
      </Panel>

      <Panel glow={false} className="p-0">
        {!rows ? <div className="space-y-2 p-4">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
          : rows.length === 0 ? <div className="grid place-items-center py-16 text-center"><Sparkles className="mb-3 size-6 text-teal-300" /><p className="text-slate-200">Every claim has an answer</p></div>
          : <ul className="divide-y divide-white/[0.05]">{rows.map((r) => <Row key={r.claim_id} r={r} />)}</ul>}
      </Panel>
    </>
  );
}

function Row({ r }: { r: ArRow }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button onClick={() => setOpen(!open)} className="grid w-full gap-3 px-4 py-4 text-left transition hover:bg-white/[0.025] md:grid-cols-[110px_minmax(0,1fr)_120px_150px_minmax(0,1fr)_20px] md:items-center">
        <span className="font-mono text-sm text-white">{r.claim_number}</span>
        <span className="min-w-0"><span className="block truncate text-sm text-slate-100">{r.patient_name}</span>
          <span className="block truncate text-xs text-slate-500">{r.payer_name} · DOS {shortDate(r.service_date)}</span></span>
        <span className="text-sm tabular-nums text-white">{money(r.amount)}</span>
        <span className="flex items-center gap-2 text-xs"><span className={cn("size-2 rounded-full", BUCKET_TONE[r.bucket])} />{r.age_days} days
          {r.days_to_filing_deadline <= 30 && <span className="rounded bg-rose-500/15 px-1.5 py-0.5 text-[10px] text-rose-200">filing in {r.days_to_filing_deadline}d</span>}</span>
        <span className="truncate text-xs">
          {r.last_status ? <span className={cn("rounded-md px-1.5 py-0.5 font-mono", STATUS_TONE[r.last_status.category])}>{r.last_status.category}</span> : null}
          <span className="ml-2 text-slate-400">{r.last_status ? r.last_status.code_text : r.due_for_follow_up ? "Not checked yet" : "Too early to chase"}</span>
        </span>
        <ChevronDown className={cn("size-4 text-slate-500 transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence>{open && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden"><Detail claimId={r.claim_id} /></motion.div>}</AnimatePresence>
    </li>
  );
}

function Detail({ claimId }: { claimId: number }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data: d } = useQuery({ queryKey: ["ar", "detail", claimId], queryFn: () => api.arDetail(claimId) });
  const [raw, setRaw] = useState(false);
  const refresh = (data: Awaited<ReturnType<typeof api.arDetail>>) => {
    queryClient.setQueryData(["ar", "detail", claimId], data);
    queryClient.invalidateQueries({ queryKey: ["ar"] });
  };
  const check = useMutation({ mutationFn: () => api.arCheck(claimId), onSuccess: (data) => { refresh(data); toast.success("Payer answered the status check"); }, onError: (e) => toast.error(e.message) });
  const act = useMutation({ mutationFn: (action: string) => api.arAction(claimId, action),
    onSuccess: (data) => { refresh(data); toast.success(data.actions[data.action_taken ?? ""] ?? "Done", { description: "The payer's answer will arrive as an 835." }); }, onError: (e) => toast.error(e.message) });
  if (!d) return <div className="p-4"><Skeleton className="h-24 rounded-xl" /></div>;
  const last = d.checks.at(-1);
  const canAct = user?.role === "biller" || user?.role === "admin";
  return (
    <div className="space-y-4 bg-white/[0.015] px-4 pb-5 pt-1">
      <div className="flex flex-wrap items-center gap-3 text-xs text-slate-400">
        <span>First sent {shortDate(d.submitted_on)} as {d.original_reference}</span>·<span>Filing deadline {shortDate(d.filing_deadline)}</span>·
        <Link href={`/claims/${d.claim_id}`} className="text-teal-300 hover:underline">Open the claim</Link>
        {canAct && <Button variant="secondary" className="ml-auto" loading={check.isPending} onClick={() => check.mutate()}><Radar className="size-4" /> Check status (276)</Button>}
      </div>
      {last && (
        <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
          <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-4">
            <div className="mb-2 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-sky-300/80"><Bot className="size-4" /> A/R Follow-up Agent</div>
            <p className="text-sm text-white"><span className={cn("mr-2 rounded-md px-1.5 py-0.5 font-mono text-xs", STATUS_TONE[last.status.category])}>{last.status.category}:{last.status.code}</span>
              {last.status.category_text}: {last.status.code_text}</p>
            {last.status.paid_amount != null && <p className="mt-1 text-xs text-emerald-200">Paid {money(last.status.paid_amount)} · check/EFT {last.status.check_number}</p>}
            <ul className="mt-3 space-y-1.5">{last.reasons.map((x, i) => <li key={i} className="flex gap-2 text-sm text-slate-200"><span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-sky-400" />{x}</li>)}</ul>
            {canAct && d.claim_status === "submitted" && (
              <div className="mt-4 flex flex-wrap gap-2">
                <Button variant="primary" loading={act.isPending} onClick={() => act.mutate(last.action)}><Send className="size-4" /> {d.actions[last.action]}</Button>
              </div>
            )}
            {d.action_taken && <p className="mt-3 text-xs text-slate-400">Done: {d.actions[d.action_taken]}. Next check {d.next_check_on ? shortDate(d.next_check_on) : "when the 835 arrives"}.</p>}
          </div>
          <div className="rounded-2xl bg-[#f7f6f2] p-4 text-[13px] leading-relaxed text-slate-800">
            <p className="text-[11px] uppercase tracking-wider text-slate-500">{last.note.channel ?? "message"} · {last.note.drafted_by === "agent" ? "drafted by the agent, fact-checked" : "template"}</p>
            {last.note.subject && <p className="mt-1 font-semibold text-slate-900">{last.note.subject}</p>}
            <p className="mt-2 whitespace-pre-wrap">{last.note.message}</p>
            {last.note.rejected_because?.length ? <p className="mt-2 text-[11px] text-rose-700">The agent&apos;s draft mentioned {last.note.rejected_because.join(", ")}, which isn&apos;t in the record, so the template was used.</p> : null}
          </div>
        </motion.div>
      )}
      {last && (
        <div>
          <button onClick={() => setRaw(!raw)} className="flex items-center gap-1.5 text-xs text-slate-400 hover:text-white"><Binary className="size-3.5" /> {raw ? "Hide" : "Show"} the 276 and 277</button>
          {raw && <div className="mt-2 grid gap-3 lg:grid-cols-2">{[last.x12_276, last.x12_277].map((x, i) => (
            <pre key={i} className="max-h-64 overflow-auto rounded-xl border border-white/[0.06] bg-ink-950/80 p-3 font-mono text-[11px] text-slate-300">{x}</pre>))}</div>}
        </div>
      )}
    </div>
  );
}
