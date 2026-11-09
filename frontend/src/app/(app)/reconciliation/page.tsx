"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Banknote, CheckCircle2, FileUp, Landmark, SearchX, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { StatCard } from "@/components/stat-card";
import { Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type Deposit } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const TONE: Record<Deposit["status"], [string, string]> = {
  matched: ["Matched", "text-emerald-200 bg-emerald-400/10 ring-emerald-400/25"],
  amount_differs: ["Amount differs", "text-amber-200 bg-amber-400/10 ring-amber-400/30"],
  unmatched: ["No 835", "text-rose-200 bg-rose-500/15 ring-rose-400/30"],
  resolved: ["Resolved", "text-slate-300 bg-white/[0.05] ring-white/10"],
};

export default function ReconciliationPage() {
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const { data } = useQuery({ queryKey: ["recon"], queryFn: api.reconciliation });
  const done = (r: { added: number; matched: number; amount_differs: number; unmatched: number }) => {
    queryClient.invalidateQueries({ queryKey: ["recon"] });
    toast.success(`${r.added} deposit(s) imported`, { description: `${r.matched} matched, ${r.amount_differs + r.unmatched} exception(s)` });
  };
  const demo = useMutation({ mutationFn: api.loadDemoBankStatement, onSuccess: done, onError: (e) => toast.error(e.message) });
  const upload = useMutation({ mutationFn: api.importBankStatement, onSuccess: done, onError: (e) => toast.error(e.message) });
  const exceptions = (data?.deposits ?? []).filter((d) => d.status === "amount_differs" || d.status === "unmatched");
  return (
    <>
      <PageHeader eyebrow="Did the money arrive?" title="Bank reconciliation"
        description="Payers send the money by EFT and the details in an 835. The bank deposit carries the same trace number, so each deposit is matched to its 835: exact matches are posted, and short payments, money without an 835, and 835s whose money never came are listed for a person. Rules only: this has to add up to the cent."
        actions={<>
          <Button variant="secondary" loading={upload.isPending} onClick={() => input.current?.click()}><FileUp className="size-4" /> Import bank CSV</Button>
          <Button variant="primary" loading={demo.isPending} onClick={() => demo.mutate()}><Landmark className="size-4" /> Load demo bank statement</Button>
          <input ref={input} type="file" accept=".csv,text/csv" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
        </>} />
      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Banknote} tone="teal" label="Deposited" value={data?.deposited ?? 0} format={(v) => money(v)} foot={`${data?.deposits.length ?? 0} deposits imported`} />
        <StatCard icon={CheckCircle2} tone="emerald" label="Matched to an 835" value={data?.matched ?? 0} foot="posted with confidence" />
        <StatCard icon={AlertTriangle} tone="amber" label="Exceptions" value={(data?.amount_differs ?? 0) + (data?.unmatched ?? 0)} foot="short payments, money without an 835" />
        <StatCard icon={SearchX} tone="rose" label="835s with no deposit" value={data?.remittances_without_deposit ?? 0} foot="paid on paper, not in the bank" />
      </motion.div>

      {!data ? <Skeleton className="h-64 rounded-2xl" /> : data.deposits.length === 0 ? (
        <Panel className="grid place-items-center py-16 text-center"><Sparkles className="mb-3 size-6 text-teal-300" />
          <p className="text-slate-200">No bank statement imported yet</p><p className="text-sm text-slate-500">Load the demo statement to see the matching.</p></Panel>
      ) : (
        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
          <Panel glow={false} className="p-0">
            <ul className="divide-y divide-white/[0.05]">
              {data.deposits.map((d, i) => (
                <motion.li key={d.id} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: Math.min(i, 15) * 0.02 }}
                  className="grid gap-2 px-4 py-3 md:grid-cols-[90px_100px_minmax(0,1fr)_130px] md:items-center">
                  <span className="text-xs text-slate-400">{shortDate(d.posted_on)}</span>
                  <span className="text-sm tabular-nums text-white">{money(d.amount)}</span>
                  <span className="truncate font-mono text-[11px] text-slate-500" title={d.description}>{d.trace_number ?? d.bank_reference}</span>
                  <span><span className={cn("rounded-full px-2.5 py-1 text-xs ring-1 ring-inset", TONE[d.status][1])}>{TONE[d.status][0]}</span></span>
                </motion.li>
              ))}
            </ul>
          </Panel>
          <div className="space-y-4">
            <Panel className="p-5">
              <p className="mb-3 font-medium text-white">Exceptions to work</p>
              {exceptions.length === 0 ? <p className="text-sm text-emerald-200/80">None open.</p> : (
                <ul className="space-y-3">{exceptions.map((d) => <Exception key={d.id} d={d} />)}</ul>
              )}
            </Panel>
            {!!data.missing_deposits.length && (
              <Panel className="p-5">
                <p className="mb-3 font-medium text-white">835s whose money never arrived</p>
                <ul className="space-y-2">{data.missing_deposits.map((m) => (
                  <li key={m.trace_number} className="rounded-xl bg-rose-500/[0.06] p-3 text-sm">
                    <p className="text-white">{money(m.amount)} from {m.payer} <span className="text-xs text-slate-400">· paid {shortDate(m.paid_on)}</span></p>
                    <p className="font-mono text-[11px] text-slate-500">trace {m.trace_number}</p>
                    <p className="mt-1 text-xs text-slate-300">Ask the payer to trace the EFT. {m.claims.map((c) => <Link key={c} href={`/claims/${c}`} className="text-teal-300 hover:underline">Claim</Link>)}</p>
                  </li>))}</ul>
              </Panel>
            )}
          </div>
        </div>
      )}
    </>
  );
}

function Exception({ d }: { d: Deposit }) {
  const queryClient = useQueryClient();
  const [note, setNote] = useState("");
  const resolve = useMutation({ mutationFn: () => api.resolveDeposit(d.id, note), onSuccess: () => { queryClient.invalidateQueries({ queryKey: ["recon"] }); toast.success("Resolved"); }, onError: (e) => toast.error(e.message) });
  return (
    <li className={cn("rounded-xl p-3", d.status === "unmatched" ? "bg-rose-500/[0.06]" : "bg-amber-400/[0.06]")}>
      <p className="text-sm text-white">{money(d.amount)} on {shortDate(d.posted_on)} <span className={cn("ml-1 rounded-full px-2 py-0.5 text-[10px] ring-1", TONE[d.status][1])}>{TONE[d.status][0]}</span></p>
      <p className="mt-1 text-xs text-slate-300">{d.note}</p>
      <div className="mt-2 flex gap-2">
        <input value={note} onChange={(e) => setNote(e.target.value)} placeholder="What did you do?" className="min-w-0 flex-1 rounded-lg border border-white/10 bg-white/[0.03] px-2 py-1 text-xs text-white outline-none" />
        <Button className="px-2.5 py-1 text-xs" disabled={!note.trim()} loading={resolve.isPending} onClick={() => resolve.mutate()}>Resolve</Button>
      </div>
    </li>
  );
}
