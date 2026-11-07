"use client";

import { CalendarClock, FilePenLine, HandCoins, RefreshCcw, Scale, Search, Trash2 } from "lucide-react";
import type { DenialAction, DenialStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

export const ACTION_META: Record<DenialAction, { label: string; icon: typeof Scale; tone: string }> = {
  appeal: { label: "Appeal", icon: Scale, tone: "text-violet-200 bg-violet-400/10 ring-violet-400/30" },
  corrected_claim: { label: "Corrected claim", icon: RefreshCcw, tone: "text-cyan-200 bg-cyan-400/10 ring-cyan-400/30" },
  bill_patient: { label: "Bill patient", icon: HandCoins, tone: "text-amber-200 bg-amber-400/10 ring-amber-400/30" },
  write_off: { label: "Write off", icon: Trash2, tone: "text-slate-300 bg-slate-400/10 ring-slate-400/25" },
  manual_review: { label: "Review", icon: Search, tone: "text-slate-200 bg-white/[0.06] ring-white/15" },
};

export function ActionChip({ action, className }: { action: DenialAction | null; className?: string }) {
  if (!action) return <span className="text-xs text-slate-500">Analysing…</span>;
  const meta = ACTION_META[action];
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-1 text-xs font-medium ring-1 ring-inset", meta.tone, className)}>
      <meta.icon className="size-3.5" /> {meta.label}
    </span>
  );
}

const STATUS_TONE: Record<DenialStatus, string> = {
  triage: "text-cyan-200 bg-cyan-400/10 ring-cyan-400/25",
  open: "text-amber-200 bg-amber-400/10 ring-amber-400/25",
  appeal_sent: "text-violet-200 bg-violet-400/10 ring-violet-400/25",
  resubmitted: "text-sky-200 bg-sky-400/10 ring-sky-400/25",
  upheld: "text-rose-200 bg-rose-400/10 ring-rose-400/25",
  overturned: "text-emerald-100 bg-emerald-400/15 ring-emerald-300/30",
  paid_on_resubmission: "text-emerald-100 bg-emerald-400/15 ring-emerald-300/30",
  patient_billed: "text-teal-200 bg-teal-400/10 ring-teal-400/25",
  written_off: "text-slate-300 bg-slate-400/10 ring-slate-400/20",
  superseded: "text-slate-400 bg-slate-400/5 ring-slate-400/15",
};
const LIVE = new Set<DenialStatus>(["triage", "appeal_sent", "resubmitted"]);

export function DenialStatusPill({ status, label }: { status: DenialStatus; label: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", STATUS_TONE[status])}>
      {LIVE.has(status) && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {label}
    </span>
  );
}

/** Days until the action is due, coloured by urgency. */
export function Deadline({ days, date, compact }: { days: number | null; date: string | null; compact?: boolean }) {
  if (days == null || !date) return <span className="text-xs text-slate-500">No deadline</span>;
  const tone = days < 0 ? "text-rose-200 bg-rose-500/15 ring-rose-400/40"
    : days <= 14 ? "text-orange-200 bg-orange-400/10 ring-orange-400/30"
    : days <= 45 ? "text-amber-100 bg-amber-300/10 ring-amber-300/25"
    : "text-slate-300 bg-white/[0.04] ring-white/10";
  const text = days < 0 ? `${-days}d overdue` : days === 0 ? "Due today" : `${days}d left`;
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-lg px-2 py-1 text-xs font-medium tabular-nums ring-1 ring-inset", tone)}
      title={`Due ${date}`}>
      <CalendarClock className="size-3.5" /> {text}
      {!compact && <span className="font-normal opacity-60">· {new Date(`${date}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })}</span>}
    </span>
  );
}

export function CodeBadge({ code, className }: { code: string; className?: string }) {
  const patient = code.startsWith("PR");
  return (
    <span className={cn("inline-flex items-center rounded-lg px-2 py-1 font-mono text-xs font-semibold ring-1 ring-inset",
      patient ? "text-amber-200 bg-amber-400/10 ring-amber-400/25" : "text-orange-200 bg-orange-500/10 ring-orange-400/30", className)}>
      {code}
    </span>
  );
}

export function DraftIcon() {
  return <FilePenLine className="size-3.5" />;
}
