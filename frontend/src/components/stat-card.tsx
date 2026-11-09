"use client";

import type { LucideIcon } from "lucide-react";
import { motion } from "motion/react";
import { AnimatedNumber, Panel, stagger } from "@/components/ui";
import { cn } from "@/lib/utils";

const TONES = {
  amber: "text-amber-300 bg-amber-400/10 ring-amber-400/20",
  sky: "text-sky-300 bg-sky-400/10 ring-sky-400/20",
  emerald: "text-emerald-300 bg-emerald-400/10 ring-emerald-400/20",
  violet: "text-violet-300 bg-violet-400/10 ring-violet-400/20",
  rose: "text-rose-300 bg-rose-400/10 ring-rose-400/20",
  teal: "text-teal-300 bg-teal-400/10 ring-teal-400/20",
};

export function StatCard({ icon: Icon, tone, label, value, foot, format }: {
  icon: LucideIcon; tone: keyof typeof TONES; label: string; value: number; foot: React.ReactNode; format?: (v: number) => string;
}) {
  return (
    <motion.div variants={stagger.item}>
      <Panel className="h-full p-5">
        <div className="flex items-start justify-between">
          <p className="text-sm text-slate-400">{label}</p>
          <div className={cn("grid size-9 place-items-center rounded-xl ring-1", TONES[tone])}><Icon className="size-[18px]" /></div>
        </div>
        <AnimatedNumber value={value} format={format} className="mt-3 block text-3xl font-semibold tracking-tight text-white" />
        <p className="mt-2 text-xs text-slate-400">{foot}</p>
      </Panel>
    </motion.div>
  );
}
