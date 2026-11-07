"use client";

import { Bot, CheckCircle2, Crosshair, ScrollText } from "lucide-react";
import { motion } from "motion/react";
import { SEVERITY_META } from "@/components/ui";
import type { Evidence, Finding } from "@/lib/api";
import { cn } from "@/lib/utils";

export function Findings({ findings, onFocus, focused }: {
  findings: Finding[];
  onFocus: (e: Evidence) => void;
  focused: Evidence | null;
}) {
  if (!findings.length) {
    return (
      <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
        className="flex items-center gap-4 rounded-2xl border border-emerald-400/20 bg-gradient-to-r from-emerald-400/10 to-transparent p-5">
        <div className="grid size-11 place-items-center rounded-xl bg-emerald-400/15 ring-1 ring-emerald-400/30">
          <CheckCircle2 className="size-6 text-emerald-300" />
        </div>
        <div>
          <p className="font-medium text-emerald-100">No issues found</p>
          <p className="text-sm text-emerald-200/60">Every check passed. A biller still approves before submission.</p>
        </div>
      </motion.div>
    );
  }

  return (
    <div className="space-y-2.5">
      {findings.map((f, i) => {
        const meta = SEVERITY_META[f.severity];
        return (
          <motion.div
            key={f.id}
            initial={{ opacity: 0, y: 12 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: i * 0.06, duration: 0.45, ease: [0.16, 1, 0.3, 1] }}
            className={cn("group relative overflow-hidden rounded-xl border border-white/[0.07] bg-white/[0.025] p-4 pl-5 transition hover:border-white/15 hover:bg-white/[0.04]")}
          >
            <span className={cn("absolute inset-y-0 left-0 w-1", meta.bar)} />
            <div className="flex flex-wrap items-center gap-2">
              <span className={cn("text-[11px] font-semibold uppercase tracking-wide", meta.text)}>{meta.label}</span>
              <span className="font-mono text-[11px] text-slate-500">{f.code}</span>
              <span className={cn("ml-auto inline-flex items-center gap-1 rounded-md px-1.5 py-0.5 text-[10px] font-medium ring-1",
                f.source === "ai" ? "bg-violet-400/10 text-violet-200 ring-violet-400/25" : "bg-white/[0.04] text-slate-300 ring-white/10")}>
                {f.source === "ai" ? <Bot className="size-3" /> : <ScrollText className="size-3" />}
                {f.source === "ai" ? "AI reviewer" : "Rule"}
              </span>
            </div>
            <p className="mt-1.5 font-medium text-white">{f.title}</p>
            <p className="mt-1 text-sm text-slate-400">{f.detail}</p>
            {!!f.evidence.length && (
              <div className="mt-3 flex flex-wrap gap-1.5">
                {f.evidence.map((e, j) => {
                  const active = focused && focused.document_id === e.document_id && focused.bbox.join() === e.bbox.join();
                  return (
                    <button key={j} onClick={() => onFocus({ ...e, label: e.label ?? f.code })}
                      className={cn("inline-flex items-center gap-1.5 rounded-lg px-2 py-1 text-xs transition",
                        active ? "bg-teal-300 text-ink-950" : "bg-teal-400/10 text-teal-200 ring-1 ring-teal-400/20 hover:bg-teal-400/20")}>
                      <Crosshair className="size-3" />
                      <span className="max-w-[220px] truncate">{e.label ?? "Show on page"}: “{e.text}”</span>
                    </button>
                  );
                })}
              </div>
            )}
          </motion.div>
        );
      })}
    </div>
  );
}
