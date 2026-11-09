"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, ClipboardList, FilePlus2, Gauge, Sparkles } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { useState } from "react";
import { AuthStatusPill, NewRequestButton, PayerClock, UrgentBadge } from "@/components/auth/bits";
import { AnimatedNumber, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type AuthSummary } from "@/lib/api";
import { cn, shortDate } from "@/lib/utils";

const VIEWS = [
  { key: "open", label: "Needs action" },
  { key: "waiting", label: "With payer" },
  { key: "done", label: "Decided" },
] as const;

export default function AuthorizationsPage() {
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("open");
  const { data: summary } = useQuery({ queryKey: ["auths", "summary"], queryFn: api.authorizationsSummary, refetchInterval: 20_000 });
  const { data: rows } = useQuery({ queryKey: ["auths", view], queryFn: () => api.authorizations(view), refetchInterval: 10_000 });

  return (
    <>
      <PageHeader eyebrow="Before the service" title="Prior authorizations"
        description="Orders for imaging and procedures. The agents check whether the payer needs authorization, whether the note meets its policy, and draft a request where every argument cites the record. Gaps go back to the physician before anything is sent."
        actions={<NewRequestButton />} />

      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat icon={FilePlus2} tone="amber" label="Needs documentation" value={summary?.needs_documentation ?? 0} foot={<>{summary?.open ?? 0} open requests</>} />
        <Stat icon={ClipboardList} tone="sky" label="With payers" value={summary?.with_payer ?? 0} foot="waiting for a decision" />
        <Stat icon={BadgeCheck} tone="emerald" label="Approved first time" value={summary?.first_pass_approval_rate ?? 0}
          format={(v) => (summary?.first_pass_approval_rate == null ? "—" : `${v.toFixed(0)}%`)}
          foot={summary?.approval_rate == null ? "no decisions yet" : `${summary.approval_rate}% approved overall`} />
        <Stat icon={Gauge} tone="violet" label="Payer turnaround" value={(summary?.avg_turnaround_seconds ?? 0) / 3600}
          format={(v) => (summary?.avg_turnaround_seconds == null ? "—" : v >= 24 ? `${(v / 24).toFixed(1)}d` : `${v.toFixed(1)}h`)}
          foot={`${summary?.not_required ?? 0} orders needed no authorization`} />
      </motion.div>

      <Panel glow={false} className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] p-3">
          <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
            {VIEWS.map((v) => (
              <button key={v.key} onClick={() => setView(v.key)}
                className={cn("relative rounded-lg px-3 py-1.5 text-sm", view === v.key ? "text-white" : "text-slate-400 hover:text-slate-200")}>
                {view === v.key && <motion.span layoutId="auth-view" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                <span className="relative">{v.label}</span>
              </button>
            ))}
          </div>
          {view === "waiting" && <p className="text-xs text-slate-500">Sorted by the payer&apos;s remaining response time</p>}
        </div>
        {!rows ? (
          <div className="space-y-2 p-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
        ) : rows.length === 0 ? (
          <div className="grid place-items-center py-20 text-center">
            <Sparkles className="mb-3 size-6 text-violet-300" />
            <p className="text-slate-200">Nothing here</p>
            <p className="text-sm text-slate-500">Start a new request from a synthetic order to watch the agents work.</p>
          </div>
        ) : (
          <motion.ul key={view} variants={stagger.container} initial="hidden" animate="show" className="divide-y divide-white/[0.05]">
            {rows.map((a) => <Row key={a.id} a={a} />)}
          </motion.ul>
        )}
      </Panel>
    </>
  );
}

function Row({ a }: { a: AuthSummary }) {
  return (
    <motion.li variants={stagger.item}>
      <Link href={`/authorizations/${a.id}`}
        className="group grid gap-3 px-4 py-4 transition-colors hover:bg-white/[0.025] md:grid-cols-[120px_minmax(0,1fr)_170px_150px_24px] md:items-center">
        <div>
          <p className="font-mono text-sm text-white">{a.number}</p>
          {a.urgency === "urgent" && <div className="mt-1"><UrgentBadge /></div>}
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm text-slate-100">{a.services.map((s) => `${s.cpt} ${s.description ?? ""}`).join(", ") || "Reading the order…"}</p>
          <p className="mt-0.5 truncate text-xs text-slate-500">{a.patient_name ?? "—"} · {a.payer_name ?? "—"} · scheduled {shortDate(a.scheduled_date)}</p>
        </div>
        <div className="flex flex-col items-start gap-1">
          <AuthStatusPill status={a.status} label={a.status_label} />
          {a.status === "needs_documentation" && a.missing > 0 && <span className="text-[11px] text-amber-200/80">{a.missing} criterion{a.missing > 1 ? "a" : ""} missing</span>}
        </div>
        <div className="text-xs text-slate-400">
          {a.status === "submitted" ? <PayerClock hours={a.hours_left} urgent={a.urgency === "urgent"} />
            : a.auth_number ? <span className="font-mono text-emerald-300">{a.auth_number}</span>
            : a.action_label ?? "—"}
        </div>
        <ArrowRight className="hidden size-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-slate-300 md:block" />
      </Link>
    </motion.li>
  );
}

const TONES = {
  amber: "text-amber-300 bg-amber-400/10 ring-amber-400/20",
  sky: "text-sky-300 bg-sky-400/10 ring-sky-400/20",
  emerald: "text-emerald-300 bg-emerald-400/10 ring-emerald-400/20",
  violet: "text-violet-300 bg-violet-400/10 ring-violet-400/20",
};

function Stat({ icon: Icon, tone, label, value, foot, format }: {
  icon: typeof Gauge; tone: keyof typeof TONES; label: string; value: number; foot: React.ReactNode; format?: (v: number) => string;
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
