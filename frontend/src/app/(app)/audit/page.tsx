"use client";

import { useQuery } from "@tanstack/react-query";
import { Link2, Lock, ShieldAlert, ShieldCheck } from "lucide-react";
import { motion } from "motion/react";
import { PageHeader, Panel, Skeleton } from "@/components/ui";
import { api } from "@/lib/api";
import { cn, timeAgo } from "@/lib/utils";

const ACTION_TONE: Record<string, string> = {
  "claim.approve": "text-emerald-300 bg-emerald-400/10",
  "claim.reject": "text-rose-300 bg-rose-400/10",
  "claim.submitted": "text-teal-300 bg-teal-400/10",
  "claim.field_corrected": "text-sky-300 bg-sky-400/10",
  "claim.created": "text-cyan-300 bg-cyan-400/10",
  "auth.login": "text-slate-300 bg-white/[0.05]",
};

export default function AuditPage() {
  const { data: entries } = useQuery({ queryKey: ["audit"], queryFn: api.audit, refetchInterval: 15_000 });
  const { data: verify, isFetching } = useQuery({ queryKey: ["audit-verify"], queryFn: api.verifyAudit, refetchInterval: 30_000 });

  return (
    <>
      <PageHeader
        eyebrow="Compliance"
        title="Audit trail"
        description="Every action by a person or an agent. Each entry is chained to the previous one by a SHA-256 hash, so any edit or deletion is detectable."
      />

      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className={cn("glow-border mb-5 flex items-center gap-4 rounded-2xl border p-5",
          verify?.ok === false ? "border-rose-400/30 bg-rose-500/10" : "border-emerald-400/20 bg-gradient-to-r from-emerald-400/10 via-teal-400/5 to-transparent")}>
        <div className={cn("relative grid size-12 place-items-center rounded-xl ring-1", verify?.ok === false ? "bg-rose-500/20 ring-rose-400/40" : "bg-emerald-400/15 ring-emerald-400/30")}>
          {isFetching && <span className="absolute inset-0 animate-pulse-ring rounded-xl ring-2 ring-emerald-300/40" />}
          {verify?.ok === false ? <ShieldAlert className="size-6 text-rose-300" /> : <ShieldCheck className="size-6 text-emerald-300" />}
        </div>
        <div>
          <p className="font-medium text-white">
            {!verify ? "Verifying chain…" : verify.ok ? "Chain verified" : `Chain broken at entry #${verify.broken_at}`}
          </p>
          <p className="text-sm text-slate-400">
            {verify ? `${verify.entries.toLocaleString()} entries re-hashed from the first one.` : "Recomputing every hash."}
            {verify?.ok && " Nothing has been altered."}
          </p>
        </div>
      </motion.div>

      <Panel glow={false} className="p-0">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[860px] text-sm">
            <thead>
              <tr className="border-b border-white/[0.06] text-left text-[11px] uppercase tracking-wider text-slate-500">
                <th className="px-5 py-3 font-medium">#</th>
                <th className="px-3 py-3 font-medium">When</th>
                <th className="px-3 py-3 font-medium">Actor</th>
                <th className="px-3 py-3 font-medium">Action</th>
                <th className="px-3 py-3 font-medium">Entity</th>
                <th className="px-3 py-3 font-medium">Details</th>
                <th className="px-5 py-3 font-medium">Hash</th>
              </tr>
            </thead>
            <tbody>
              {!entries && Array.from({ length: 10 }).map((_, i) => <tr key={i}><td colSpan={7} className="px-5 py-2"><Skeleton className="h-7" /></td></tr>)}
              {entries?.map((e, i) => (
                <motion.tr key={e.id} initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: Math.min(i, 20) * 0.02 }}
                  className="border-b border-white/[0.04] hover:bg-white/[0.02]">
                  <td className="px-5 py-3 font-mono text-xs text-slate-500">{e.id}</td>
                  <td className="px-3 py-3 text-xs text-slate-400" title={e.at}>{timeAgo(e.at)}</td>
                  <td className="px-3 py-3 text-slate-200">{e.actor}</td>
                  <td className="px-3 py-3">
                    <span className={cn("rounded-md px-2 py-0.5 font-mono text-[11px]", ACTION_TONE[e.action] ?? "bg-violet-400/10 text-violet-300")}>{e.action}</span>
                  </td>
                  <td className="px-3 py-3 font-mono text-xs text-slate-300">{e.entity_id}</td>
                  <td className="max-w-[280px] truncate px-3 py-3 text-xs text-slate-500" title={JSON.stringify(e.details)}>
                    {Object.entries(e.details).filter(([, v]) => v != null && v !== "").map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`).join(" · ") || "—"}
                  </td>
                  <td className="px-5 py-3">
                    <span className="inline-flex items-center gap-1.5 font-mono text-[11px] text-slate-500" title={`prev ${e.prev_hash}`}>
                      {i === (entries.length - 1) ? <Lock className="size-3" /> : <Link2 className="size-3 text-teal-400/70" />}
                      {e.hash.slice(0, 10)}…
                    </span>
                  </td>
                </motion.tr>
              ))}
            </tbody>
          </table>
        </div>
      </Panel>
    </>
  );
}
