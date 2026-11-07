"use client";

import { Check, FileSearch, Inbox, Send, ShieldCheck, Sparkles, UserCheck, X } from "lucide-react";
import { motion } from "motion/react";
import type { ClaimStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const STEPS = [
  { id: "received", label: "Received", icon: Inbox },
  { id: "extracting", label: "Reading", icon: FileSearch },
  { id: "validating", label: "Validating", icon: Sparkles },
  { id: "review", label: "Human review", icon: UserCheck },
  { id: "approved", label: "Approved", icon: ShieldCheck },
  { id: "submitted", label: "Submitted", icon: Send },
];

const POSITION: Record<ClaimStatus, number> = {
  received: 0, extracting: 1, validating: 2, needs_review: 3, ready: 3, approved: 4, submitted: 5, rejected: 4, failed: 1,
};

export function Lifecycle({ status }: { status: ClaimStatus }) {
  const current = POSITION[status];
  const ended = status === "submitted";
  const stopped = status === "rejected" || status === "failed";
  const progress = (current / (STEPS.length - 1)) * 100;

  return (
    <div className="relative px-2 py-2">
      <div className="absolute left-[calc(8.33%)] right-[calc(8.33%)] top-[26px] h-[2px] rounded-full bg-white/[0.06]">
        <motion.div
          className={cn("relative h-full overflow-hidden rounded-full", stopped ? "bg-rose-400/70" : "bg-gradient-to-r from-teal-400 via-cyan-400 to-violet-400")}
          initial={{ width: 0 }}
          animate={{ width: `${progress}%` }}
          transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
        >
          {!ended && !stopped && <span className="absolute inset-y-0 w-1/3 animate-beam bg-gradient-to-r from-transparent via-white/80 to-transparent" />}
        </motion.div>
      </div>
      <div className="relative grid grid-cols-6">
        {STEPS.map((step, i) => {
          const done = i < current || (ended && i === current);
          const active = i === current && !ended;
          const failedHere = stopped && i === current;
          const Icon = failedHere ? X : done ? Check : step.icon;
          const label = failedHere ? (status === "rejected" ? "Rejected" : "Failed") : step.label;
          return (
            <div key={step.id} className="flex flex-col items-center gap-2">
              <motion.div
                initial={{ scale: 0.6, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ delay: i * 0.08, type: "spring", stiffness: 300, damping: 20 }}
                className={cn(
                  "relative grid size-[38px] place-items-center rounded-xl ring-1 transition-colors duration-500",
                  failedHere ? "bg-rose-500/20 text-rose-200 ring-rose-400/40"
                    : done ? "bg-gradient-to-br from-teal-400 to-cyan-500 text-ink-950 ring-transparent shadow-[0_0_20px_-4px_rgba(45,212,191,0.8)]"
                    : active ? "bg-ink-800 text-white ring-teal-300/60"
                    : "bg-ink-900 text-slate-500 ring-white/10",
                )}
              >
                {active && !failedHere && <span className="absolute inset-0 animate-pulse-ring rounded-xl ring-2 ring-teal-300/60" />}
                <Icon className="size-[17px]" strokeWidth={done ? 3 : 2} />
              </motion.div>
              <span className={cn("text-center text-[11px] font-medium", failedHere ? "text-rose-300" : done || active ? "text-slate-200" : "text-slate-500")}>
                {label}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
