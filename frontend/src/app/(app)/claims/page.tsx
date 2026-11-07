"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Inbox, Search } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useDeferredValue, useState } from "react";
import { NewClaimButton } from "@/components/upload-dialog";
import { PageHeader, Panel, RiskBadge, Skeleton, StatusPill } from "@/components/ui";
import { api } from "@/lib/api";
import { cn, money, shortDate, timeAgo } from "@/lib/utils";

const VIEWS = [
  { id: "all", label: "All", status: undefined },
  { id: "review", label: "Needs decision", status: "needs_review,ready" },
  { id: "progress", label: "In progress", status: "received,extracting,validating,approved" },
  { id: "submitted", label: "Submitted", status: "submitted" },
  { id: "rejected", label: "Rejected", status: "rejected,failed" },
] as const;

function ClaimsInner() {
  const router = useRouter();
  const params = useSearchParams();
  const [view, setView] = useState<string>(params.get("view") ?? "all");
  const [q, setQ] = useState("");
  const query = useDeferredValue(q);
  const status = VIEWS.find((v) => v.id === view)?.status;
  const { data, isLoading } = useQuery({
    queryKey: ["claims", view, query],
    queryFn: () => api.claims({ status, q: query }),
    refetchInterval: 15_000,
  });

  return (
    <>
      <PageHeader
        eyebrow="Work queue"
        title="Claims"
        description="Every claim, where it is in the pipeline, and how risky the agents think it is."
        actions={<NewClaimButton />}
      />

      <div className="mb-4 flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div className="flex gap-1 overflow-x-auto rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
          {VIEWS.map((v) => (
            <button key={v.id} onClick={() => setView(v.id)}
              className={cn("relative whitespace-nowrap rounded-lg px-3.5 py-1.5 text-sm transition-colors", view === v.id ? "text-white" : "text-slate-400 hover:text-slate-200")}>
              {view === v.id && <motion.span layoutId="claims-tab" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" transition={{ type: "spring", stiffness: 400, damping: 32 }} />}
              <span className="relative">{v.label}</span>
            </button>
          ))}
        </div>
        <div className="relative md:w-80">
          <Search className="absolute left-3 top-1/2 size-4 -translate-y-1/2 text-slate-500" />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search claim, patient or payer"
            className="w-full rounded-xl border border-white/10 bg-white/[0.03] py-2 pl-9 pr-3 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-teal-400/40 focus:ring-4 focus:ring-teal-400/10" />
        </div>
      </div>

      <Panel glow={false} className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[900px] text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-left text-[11px] uppercase tracking-wider text-slate-500">
                <th className="px-5 py-3 font-medium">Claim</th>
                <th className="px-3 py-3 font-medium">Patient</th>
                <th className="px-3 py-3 font-medium">Payer</th>
                <th className="px-3 py-3 font-medium">Service date</th>
                <th className="px-3 py-3 text-right font-medium">Amount</th>
                <th className="px-3 py-3 text-center font-medium">Findings</th>
                <th className="px-3 py-3 font-medium">Risk</th>
                <th className="px-3 py-3 font-medium">Status</th>
                <th className="px-5 py-3" />
              </tr>
            </thead>
            <tbody>
              {isLoading && Array.from({ length: 8 }).map((_, i) => (
                <tr key={i}><td colSpan={9} className="px-5 py-2.5"><Skeleton className="h-8" /></td></tr>
              ))}
              <AnimatePresence initial={false}>
                {data?.map((c, i) => (
                  <motion.tr
                    key={c.id}
                    layout
                    initial={{ opacity: 0, y: 8 }}
                    animate={{ opacity: 1, y: 0, transition: { delay: Math.min(i, 15) * 0.025 } }}
                    exit={{ opacity: 0 }}
                    onClick={() => router.push(`/claims/${c.id}`)}
                    className="group cursor-pointer border-b border-white/[0.04] transition-colors hover:bg-gradient-to-r hover:from-teal-400/[0.05] hover:to-transparent"
                  >
                    <td className="px-5 py-3.5">
                      <p className="font-mono text-[13px] text-white">{c.number}</p>
                      <p className="text-[11px] text-slate-500">{c.source === "demo-history" ? "Demo history" : c.source === "sample" ? "Sample packet" : "Uploaded"} · {timeAgo(c.created_at)}</p>
                    </td>
                    <td className="px-3 py-3.5 text-slate-200">{c.patient_name ?? <span className="text-slate-500">Reading…</span>}</td>
                    <td className="px-3 py-3.5 text-slate-400">{c.payer_name ?? "—"}</td>
                    <td className="px-3 py-3.5 text-slate-400">{shortDate(c.service_date)}</td>
                    <td className="px-3 py-3.5 text-right font-medium tabular-nums text-slate-100">{money(c.total_charge)}</td>
                    <td className="px-3 py-3.5 text-center">
                      {c.finding_count ? (
                        <span className="inline-grid min-w-6 place-items-center rounded-md bg-orange-400/10 px-1.5 py-0.5 text-xs font-semibold text-orange-200 ring-1 ring-orange-400/20">{c.finding_count}</span>
                      ) : <span className="text-xs text-slate-600">0</span>}
                    </td>
                    <td className="px-3 py-3.5"><RiskBadge risk={c.risk} /></td>
                    <td className="px-3 py-3.5"><StatusPill status={c.status} /></td>
                    <td className="px-5 py-3.5 text-right"><ChevronRight className="ml-auto size-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-teal-300" /></td>
                  </motion.tr>
                ))}
              </AnimatePresence>
            </tbody>
          </table>
          {data && !data.length && (
            <div className="grid place-items-center py-16 text-center">
              <Inbox className="mb-3 size-8 text-slate-600" />
              <p className="text-sm text-slate-300">No claims here</p>
              <p className="text-xs text-slate-500">Try another tab, or create a claim from a sample packet.</p>
            </div>
          )}
        </div>
      </Panel>
    </>
  );
}

export default function ClaimsPage() {
  return <Suspense><ClaimsInner /></Suspense>;
}
