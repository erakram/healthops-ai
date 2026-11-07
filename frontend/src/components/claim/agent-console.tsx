"use client";

import { Bot, Cpu, Workflow } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef } from "react";
import type { ClaimEvent } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Live log of what the agents are doing, shown while a claim is being processed. */
export function AgentConsole({ events, running }: { events: ClaimEvent[]; running: boolean }) {
  const steps = events.filter((e) => e.type === "step" || e.type === "transition");
  const end = useRef<HTMLDivElement>(null);
  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "nearest" });
  }, [steps.length]);

  return (
    <div className="relative overflow-hidden rounded-2xl border border-cyan-400/20 bg-ink-950/80">
      {running && <div className="absolute inset-x-0 top-0 h-px overflow-hidden"><div className="h-full w-1/3 animate-beam bg-gradient-to-r from-transparent via-cyan-300 to-transparent" /></div>}
      <div className="flex items-center justify-between border-b border-white/[0.06] px-4 py-2.5">
        <div className="flex items-center gap-2 text-xs font-medium text-slate-300">
          <Cpu className="size-3.5 text-cyan-300" /> Agent console
        </div>
        <span className={cn("flex items-center gap-1.5 text-[11px]", running ? "text-cyan-300" : "text-slate-500")}>
          <span className={cn("size-1.5 rounded-full", running ? "animate-pulse bg-cyan-300" : "bg-slate-600")} />
          {running ? "working" : "idle"}
        </span>
      </div>
      <div className="max-h-56 space-y-1.5 overflow-y-auto p-4 font-mono text-[12px]">
        <AnimatePresence initial={false}>
          {steps.map((e) => {
            const agent = e.actor.includes("Agent");
            return (
              <motion.div key={e.id} initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} className="flex gap-2.5">
                <span className="shrink-0 text-slate-600">{new Date(e.at).toLocaleTimeString("en-US", { hour12: false })}</span>
                {agent ? <Bot className="mt-0.5 size-3.5 shrink-0 text-cyan-300" /> : <Workflow className="mt-0.5 size-3.5 shrink-0 text-violet-300" />}
                <span className={cn("shrink-0", agent ? "text-cyan-300" : "text-violet-300")}>{e.actor.split("@")[0]}</span>
                <span className="text-slate-300">{e.message}</span>
              </motion.div>
            );
          })}
        </AnimatePresence>
        {running && (
          <div className="flex items-center gap-2 pl-[70px] text-slate-500">
            <span className="inline-block h-3.5 w-2 animate-pulse bg-cyan-300/70" />
          </div>
        )}
        <div ref={end} />
      </div>
    </div>
  );
}
