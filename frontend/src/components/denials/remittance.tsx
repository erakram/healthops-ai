"use client";

import { useQuery } from "@tanstack/react-query";
import { Binary, ChevronDown, Receipt } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useState } from "react";
import { api, type Remittance } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const KIND_LABEL: Record<Remittance["kind"], string> = {
  original: "Original claim", corrected: "Corrected claim", appeal: "After appeal", imported: "Imported file",
};

function AdjChip({ group, code, amount }: { group: string; code: string; amount: number }) {
  const tone = code === "45" ? "text-slate-400 bg-white/[0.04] ring-white/10"
    : group === "PR" ? "text-amber-200 bg-amber-400/10 ring-amber-400/25"
    : "text-orange-200 bg-orange-500/10 ring-orange-400/30";
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 font-mono text-[11px] ring-1 ring-inset", tone)}>
      {group}-{code} <span className="tabular-nums opacity-80">{money(amount)}</span>
    </span>
  );
}

/** One 835 claim: what the payer paid per line and why it adjusted the rest. */
export function RemittanceCard({ remit, highlight }: { remit: Remittance; highlight?: string[] }) {
  const [raw, setRaw] = useState(false);
  const c = remit.claim;
  const reversal = c.status_code === "22";
  return (
    <div className={cn("rounded-2xl border bg-white/[0.02] p-4", reversal ? "border-white/[0.05] opacity-80" : "border-white/[0.07]")}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Receipt className="size-4 text-sky-300" />
          <p className="text-sm font-medium text-white">{KIND_LABEL[remit.kind]}</p>
          <span className={cn("rounded-md px-1.5 py-0.5 text-[11px] ring-1 ring-inset",
            reversal ? "text-slate-300 bg-white/[0.05] ring-white/10"
              : c.status_code === "4" ? "text-orange-200 bg-orange-500/10 ring-orange-400/30"
              : "text-emerald-200 bg-emerald-400/10 ring-emerald-400/25")}>
            CLP {c.status_code} · {c.status}
          </span>
        </div>
        <p className="text-xs text-slate-500">{remit.payer_name} · trace {remit.trace_number} · {shortDate(remit.received_at)}</p>
      </div>

      <div className="mt-3 overflow-x-auto">
        <table className="w-full min-w-[520px] text-left text-xs">
          <thead className="text-[10px] uppercase tracking-wider text-slate-500">
            <tr><th className="py-1.5 font-medium">Service</th><th className="font-medium text-right">Billed</th>
              <th className="font-medium text-right">Allowed</th><th className="font-medium text-right">Paid</th>
              <th className="pl-4 font-medium">Adjustments</th></tr>
          </thead>
          <tbody className="divide-y divide-white/[0.04]">
            {c.lines.map((l, i) => (
              <tr key={i} className={cn(highlight?.includes(l.cpt) && "bg-orange-400/[0.05]")}>
                <td className="py-2 font-mono text-slate-200">{l.cpt}{l.modifiers.length ? `-${l.modifiers.join("-")}` : ""}
                  {l.remarks.length > 0 && <span className="ml-2 font-sans text-[10px] text-slate-500">{l.remarks.join(" ")}</span>}</td>
                <td className="text-right tabular-nums text-slate-300">{money(l.charge)}</td>
                <td className="text-right tabular-nums text-slate-400">{l.allowed == null ? "—" : money(l.allowed)}</td>
                <td className={cn("text-right tabular-nums font-medium", l.paid > 0 ? "text-emerald-300" : l.paid < 0 ? "text-rose-300" : "text-slate-500")}>{money(l.paid)}</td>
                <td className="pl-4"><div className="flex flex-wrap gap-1">{l.adjustments.map((a, j) => <AdjChip key={j} {...a} />)}</div></td>
              </tr>
            ))}
          </tbody>
          <tfoot className="border-t border-white/10 text-slate-200">
            <tr><td className="py-2 font-medium">Claim</td><td className="text-right tabular-nums">{money(c.charge)}</td><td />
              <td className="text-right font-semibold tabular-nums text-white">{money(c.paid)}</td>
              <td className="pl-4 text-slate-400">{c.patient_responsibility ? <>Patient owes <span className="text-amber-200">{money(c.patient_responsibility)}</span></> : null}</td></tr>
          </tfoot>
        </table>
      </div>

      <button onClick={() => setRaw(!raw)} className="mt-2 flex items-center gap-1.5 text-xs text-slate-400 transition hover:text-white">
        <Binary className="size-3.5" /> {raw ? "Hide" : "Show"} the raw 835
        <ChevronDown className={cn("size-3.5 transition-transform", raw && "rotate-180")} />
      </button>
      <AnimatePresence>
        {raw && (
          <motion.div initial={{ opacity: 0, height: 0 }} animate={{ opacity: 1, height: "auto" }} exit={{ opacity: 0, height: 0 }} className="overflow-hidden">
            <RawEdi id={remit.id} claimNumber={c.claim_number} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

const SEGMENT_HELP: Record<string, string> = {
  ISA: "Interchange envelope", GS: "Functional group", ST: "Transaction set start", BPR: "Payment amount and method",
  TRN: "Trace number", DTM: "Date", N1: "Party (payer / payee)", LX: "Claim group", CLP: "Claim: status, charge, paid",
  CAS: "Adjustment: group, reason, amount", NM1: "Patient", SVC: "Service line", AMT: "Allowed amount",
  LQ: "Remark code", SE: "Transaction set end (segment count)", GE: "Group end", IEA: "Interchange end",
};

function RawEdi({ id, claimNumber }: { id: number; claimNumber: string }) {
  const { data } = useQuery({ queryKey: ["remit-raw", id], queryFn: () => api.remittanceRaw(id), staleTime: Infinity });
  if (!data) return <div className="skeleton mt-2 h-40 rounded-xl" />;
  const segments = data.split("~").map((s) => s.trim()).filter(Boolean);
  return (
    <div className="mt-2 max-h-80 overflow-auto rounded-xl border border-white/[0.06] bg-ink-950/80 p-3 font-mono text-[11.5px] leading-relaxed">
      {segments.map((seg, i) => {
        const [tag, ...rest] = seg.split("*");
        const key = tag === "CLP" || tag === "CAS" || tag === "SVC";
        const isClaim = tag === "CLP" && rest[0] === claimNumber;
        return (
          <div key={i} title={SEGMENT_HELP[tag]} className={cn("flex gap-3 rounded px-1", isClaim && "bg-teal-400/[0.08]", tag === "CAS" && rest[1] !== "45" && "bg-orange-400/[0.07]")}>
            <span className="w-6 shrink-0 select-none text-right text-slate-600">{i + 1}</span>
            <span className="whitespace-pre">
              <span className={cn("font-semibold", key ? "text-teal-300" : tag.match(/^(ISA|GS|ST|SE|GE|IEA)$/) ? "text-slate-500" : "text-violet-300")}>{tag}</span>
              {rest.map((el, j) => <span key={j}><span className="text-slate-600">*</span><span className="text-slate-300">{el}</span></span>)}
              <span className="text-slate-600">~</span>
            </span>
          </div>
        );
      })}
    </div>
  );
}
