"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, CalendarRange, ChevronDown, FileText, HandCoins, PiggyBank, Sparkles, Wallet } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { toast } from "sonner";
import { StatCard } from "@/components/stat-card";
import { Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type StatementRow } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const STAGE: Record<string, [string, string]> = {
  statement: ["Statement sent", "text-teal-200 bg-teal-400/10 ring-teal-400/25"],
  reminder: ["Reminder sent", "text-amber-200 bg-amber-400/10 ring-amber-400/30"],
  final_notice: ["Final notice", "text-orange-200 bg-orange-400/10 ring-orange-400/30"],
  review: ["Needs review", "text-rose-200 bg-rose-500/15 ring-rose-400/30"],
};
const STATUS: Record<string, string> = { open: "Open", partial: "Part paid", plan: "Payment plan", paid: "Paid", review: "Review" };

export default function BillingPage() {
  const queryClient = useQueryClient();
  const [view, setView] = useState<"open" | "all">("open");
  const { data: summary } = useQuery({ queryKey: ["billing", "summary"], queryFn: api.billingSummary });
  const { data: rows } = useQuery({ queryKey: ["billing", view], queryFn: () => api.statements(view) });
  const reminders = useMutation({ mutationFn: api.runReminders, onSuccess: (r) => {
    queryClient.invalidateQueries({ queryKey: ["billing"] });
    toast.success(r.moved.length ? `${r.moved.length} statement(s) moved on` : "Nothing is due for a reminder today");
  } });
  return (
    <>
      <PageHeader eyebrow="The patient's share" title="Patient billing"
        description="What patients owe after insurance: deductibles, coinsurance, and visits after coverage ended. Each line explains the reason in plain words from the payer's adjustment code. Unpaid statements get a reminder at 30 days and a final notice at 60; at 90 a person decides. Nothing goes to collections automatically."
        actions={<Button variant="secondary" loading={reminders.isPending} onClick={() => reminders.mutate()}><BellRing className="size-4" /> Run reminder cycle</Button>} />
      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Wallet} tone="amber" label="Patients owe" value={summary?.outstanding ?? 0} format={(v) => money(v)} foot={`${summary?.open ?? 0} open statements`} />
        <StatCard icon={PiggyBank} tone="emerald" label="Collected" value={summary?.collected ?? 0} format={(v) => money(v)}
          foot={summary?.collection_rate == null ? "nothing billed yet" : `${summary.collection_rate}% of what was billed`} />
        <StatCard icon={CalendarRange} tone="violet" label="On a payment plan" value={summary?.on_plan ?? 0} foot="monthly installments" />
        <StatCard icon={BellRing} tone="rose" label="Needs review" value={summary?.needs_review ?? 0} foot="90+ days unpaid" />
      </motion.div>
      <Panel glow={false} className="p-0">
        <div className="flex border-b border-white/[0.06] p-3">
          <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
            {(["open", "all"] as const).map((v) => (
              <button key={v} onClick={() => setView(v)} className={cn("relative rounded-lg px-3 py-1.5 text-sm", view === v ? "text-white" : "text-slate-400")}>
                {view === v && <motion.span layoutId="bill-view" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                <span className="relative">{v === "open" ? "Unpaid" : "All statements"}</span>
              </button>
            ))}
          </div>
        </div>
        {!rows ? <div className="space-y-2 p-4">{[0, 1].map((i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
          : rows.length === 0 ? <div className="grid place-items-center py-16"><Sparkles className="mb-3 size-6 text-teal-300" /><p className="text-slate-200">No unpaid statements</p>
            <p className="text-sm text-slate-500">Bill a patient from a deductible on the Denials page.</p></div>
          : <ul className="divide-y divide-white/[0.05]">{rows.map((s) => <Row key={s.id} s={s} />)}</ul>}
      </Panel>
    </>
  );
}

function Row({ s }: { s: StatementRow }) {
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button onClick={() => setOpen(!open)} className="grid w-full gap-3 px-4 py-4 text-left hover:bg-white/[0.025] md:grid-cols-[120px_minmax(0,1fr)_150px_150px_130px_20px] md:items-center">
        <span className="font-mono text-sm text-white">{s.number}</span>
        <span className="min-w-0"><span className="block truncate text-sm text-slate-100">{s.patient_name}</span>
          <span className="block truncate text-xs text-slate-500">{s.items.map((i) => i.reason.split(":")[0]).join(", ")}</span></span>
        <span className="text-sm"><span className="tabular-nums text-white">{money(s.balance)}</span><span className="ml-1 text-xs text-slate-500">of {money(s.total)}</span></span>
        <span><span className={cn("rounded-full px-2.5 py-1 text-xs ring-1 ring-inset", STAGE[s.stage][1])}>{s.status === "paid" ? "Paid" : STAGE[s.stage][0]}</span></span>
        <span className="text-xs text-slate-400">{STATUS[s.status]} · due {shortDate(s.due_on)}</span>
        <ChevronDown className={cn("size-4 text-slate-500 transition-transform", open && "rotate-180")} />
      </button>
      <AnimatePresence>{open && <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden"><Detail id={s.id} /></motion.div>}</AnimatePresence>
    </li>
  );
}

function Detail({ id }: { id: number }) {
  const queryClient = useQueryClient();
  const { data: s } = useQuery({ queryKey: ["billing", "statement", id], queryFn: () => api.statement(id) });
  const [amount, setAmount] = useState("");
  const [method, setMethod] = useState("card");
  const [months, setMonths] = useState(3);
  const refresh = (d: StatementRow) => { queryClient.setQueryData(["billing", "statement", id], d); queryClient.invalidateQueries({ queryKey: ["billing"] }); };
  const pay = useMutation({ mutationFn: () => api.recordPayment(id, Number(amount), method), onSuccess: (d) => { refresh(d); setAmount(""); toast.success("Payment recorded"); }, onError: (e) => toast.error(e.message) });
  const plan = useMutation({ mutationFn: () => api.setUpPlan(id, months), onSuccess: (d) => { refresh(d); toast.success("Payment plan set up"); }, onError: (e) => toast.error(e.message) });
  if (!s) return <div className="p-4"><Skeleton className="h-24 rounded-xl" /></div>;
  return (
    <div className="grid gap-4 bg-white/[0.015] px-4 pb-5 pt-1 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        <table className="w-full text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500"><tr><th className="py-1.5">Visit</th><th>Why you owe this</th><th className="text-right">Amount</th></tr></thead>
          <tbody className="divide-y divide-white/[0.04]">{s.items.map((i, k) => (
            <tr key={k}><td className="py-2 text-slate-300">{i.service_date}<br /><span className="font-mono text-[10px] text-slate-500">{i.claim_number} · {i.code}</span></td>
              <td className="pr-3 text-slate-200">{i.reason}</td><td className="text-right tabular-nums text-white">{money(i.amount)}</td></tr>))}</tbody>
        </table>
        {s.plan?.schedule && (
          <div className="rounded-xl border border-violet-400/20 bg-violet-400/[0.04] p-3">
            <p className="mb-2 text-xs font-medium text-violet-200">Payment plan · {s.plan.installments} monthly payments</p>
            <div className="flex flex-wrap gap-2">{s.plan.schedule.map((p, k) => (
              <span key={k} className={cn("rounded-lg px-2 py-1 text-xs ring-1", p.paid ? "bg-emerald-400/10 text-emerald-200 ring-emerald-400/25" : "bg-white/[0.03] text-slate-300 ring-white/10")}>
                {shortDate(p.due_on)} · {money(p.amount)}{p.paid ? " ✓" : ""}</span>))}</div>
          </div>
        )}
        <div className="flex flex-wrap gap-2">{s.letters.map((l) => (
          <a key={l.index} href={`/api/billing/statements/${s.id}/letters/${l.index}`} target="_blank"
            className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.04] px-2.5 py-1.5 text-xs text-slate-300 ring-1 ring-white/10 hover:text-white">
            <FileText className="size-3.5" /> {STAGE[l.stage]?.[0] ?? l.stage} PDF · {shortDate(l.at)}</a>))}</div>
      </div>
      <div className="space-y-3">
        {s.status !== "paid" ? (
          <>
            <div className="rounded-xl border border-white/[0.07] p-3">
              <p className="mb-2 text-xs font-medium text-slate-300">Record a payment</p>
              <div className="flex gap-2">
                <input value={amount} onChange={(e) => setAmount(e.target.value)} placeholder={s.balance.toFixed(2)} inputMode="decimal"
                  className="w-24 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1.5 text-sm text-white outline-none" />
                <select value={method} onChange={(e) => setMethod(e.target.value)} className="rounded-lg border border-white/10 bg-ink-900 px-2 text-sm text-slate-200">
                  {["card", "cash", "check", "ach"].map((m) => <option key={m}>{m}</option>)}</select>
                <Button variant="primary" className="px-3 py-1.5" disabled={!Number(amount)} loading={pay.isPending} onClick={() => pay.mutate()}><HandCoins className="size-4" /> Record</Button>
              </div>
            </div>
            {!s.plan?.schedule && (
              <div className="rounded-xl border border-white/[0.07] p-3">
                <p className="mb-2 text-xs font-medium text-slate-300">Set up a payment plan</p>
                <div className="flex items-center gap-2">
                  <select value={months} onChange={(e) => setMonths(Number(e.target.value))} className="rounded-lg border border-white/10 bg-ink-900 px-2 py-1.5 text-sm text-slate-200">
                    {[2, 3, 4, 6, 9, 12].map((m) => <option key={m} value={m}>{m} months</option>)}</select>
                  <span className="text-xs text-slate-400">≈ {money(s.balance / months)} / month</span>
                  <Button className="ml-auto px-3 py-1.5" loading={plan.isPending} onClick={() => plan.mutate()}><CalendarRange className="size-4" /> Set up</Button>
                </div>
              </div>
            )}
          </>
        ) : <p className="rounded-xl bg-emerald-400/10 p-3 text-sm text-emerald-200">Paid in full.</p>}
        {!!s.payments?.length && (
          <ul className="space-y-1 text-xs text-slate-400">{s.payments.map((p, k) => <li key={k}>{shortDate(p.received_on)} · {money(p.amount)} by {p.method}{p.reference ? ` (${p.reference})` : ""}</li>)}</ul>
        )}
      </div>
    </div>
  );
}
