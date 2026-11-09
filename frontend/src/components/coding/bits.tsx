"use client";

import type { EncStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const TONE: Record<EncStatus, string> = {
  received: "text-slate-300 bg-slate-400/10 ring-slate-400/20", reading: "text-cyan-200 bg-cyan-400/10 ring-cyan-400/25",
  coding: "text-violet-200 bg-violet-400/10 ring-violet-400/25", review: "text-amber-200 bg-amber-400/10 ring-amber-400/30",
  coded: "text-teal-100 bg-teal-400/15 ring-teal-300/30", billed: "text-emerald-100 bg-emerald-400/15 ring-emerald-300/30",
  failed: "text-rose-200 bg-rose-500/15 ring-rose-400/30",
};
const LIVE = new Set<EncStatus>(["received", "reading", "coding"]);

export function EncStatusPill({ status, label }: { status: EncStatus; label: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", TONE[status])}>
      {LIVE.has(status) && <span className="size-1.5 animate-pulse rounded-full bg-current" />}{label}
    </span>
  );
}

