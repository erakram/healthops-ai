"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, FileUp, Inbox, PiggyBank, Sparkles, TrendingUp } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useRef, useState } from "react";
import { toast } from "sonner";
import { ActionChip, CodeBadge, Deadline, DenialStatusPill } from "@/components/denials/bits";
import { AnimatedNumber, Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type DenialSummary } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const VIEWS = [
  { key: "open", label: "Needs action" },
  { key: "waiting", label: "Waiting on payer" },
  { key: "closed", label: "Resolved" },
] as const;

export default function DenialsPage() {
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("open");
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data: summary } = useQuery({ queryKey: ["denials", "summary"], queryFn: api.denialsSummary, refetchInterval: 20_000 });
  const { data: rows } = useQuery({ queryKey: ["denials", view], queryFn: () => api.denials(view), refetchInterval: 15_000 });
  const canImport = user?.role === "biller" || user?.role === "admin";

  return (
    <>
      <PageHeader
        eyebrow="Revenue recovery"
        title="Denials"
        description="Every payer adjustment someone has to act on. The Denial Triage Agent works out why, what to do and by when; the Appeal Agent drafts letters backed by the record."
        actions={canImport && <ImportButton />}
      />

      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat icon={Inbox} tone="amber" label="Needs action" value={summary?.open ?? 0} foot={<>{money(summary?.open_amount, true)} adjusted</>} />
        <Stat icon={TrendingUp} tone="violet" label="Expected recovery" value={summary?.expected_recovery ?? 0}
          format={(v) => money(v, true)} foot="if the recommendations are followed" />
        <Stat icon={CalendarClock} tone="rose" label="Due within 14 days" value={summary?.due_soon ?? 0} foot={<>{summary?.waiting ?? 0} waiting on payers</>} />
        <Stat icon={PiggyBank} tone="teal" label="Recovered" value={summary?.recovered ?? 0} format={(v) => money(v, true)}
          foot="overturned appeals and corrected claims" />
      </motion.div>

      <Panel glow={false} className="p-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-white/[0.06] p-3">
          <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
            {VIEWS.map((v) => (
              <button key={v.key} onClick={() => setView(v.key)}
                className={cn("relative rounded-lg px-3 py-1.5 text-sm", view === v.key ? "text-white" : "text-slate-400 hover:text-slate-200")}>
                {view === v.key && <motion.span layoutId="denial-view" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                <span className="relative">{v.label}</span>
              </button>
            ))}
          </div>
          {view === "open" && <p className="text-xs text-slate-500">Sorted by expected recovery, then deadline</p>}
        </div>

        {!rows ? (
          <div className="space-y-2 p-4">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
        ) : rows.length === 0 ? (
          <div className="grid place-items-center py-20 text-center">
            <Sparkles className="mb-3 size-6 text-teal-300" />
            <p className="text-slate-200">Nothing here</p>
            <p className="text-sm text-slate-500">Submit a claim from the &quot;payer will deny&quot; samples to see the agents work.</p>
          </div>
        ) : (
          <motion.ul key={view} variants={stagger.container} initial="hidden" animate="show" className="divide-y divide-white/[0.05]">
            {rows.map((d) => <Row key={d.id} d={d} />)}
          </motion.ul>
        )}
      </Panel>
    </>
  );
}

function Row({ d }: { d: DenialSummary }) {
  return (
    <motion.li variants={stagger.item}>
      <Link href={`/denials/${d.id}`}
        className="group grid gap-3 px-4 py-4 transition-colors hover:bg-white/[0.025] md:grid-cols-[110px_minmax(0,1fr)_130px_150px_130px_24px] md:items-center">
        <div className="flex items-center gap-2 md:block">
          <CodeBadge code={d.code} />
          <p className="mt-1 hidden text-[11px] text-slate-500 md:block">{d.category_label}</p>
        </div>
        <div className="min-w-0">
          <p className="truncate text-sm text-slate-100">{d.reason}</p>
          <p className="mt-0.5 truncate text-xs text-slate-500">
            <span className="font-mono text-slate-400">{d.claim_number}</span> · {d.patient_name} · {d.payer_name} · DOS {shortDate(d.service_date)}
            {" · "}{d.lines.map((l) => l.cpt).join(", ")}
          </p>
        </div>
        <div className="md:text-right">
          <p className="text-sm font-semibold tabular-nums text-white">{money(d.amount)}</p>
          {d.recovered > 0 ? <p className="text-[11px] text-emerald-300">+{money(d.recovered)} recovered</p>
            : d.expected_recovery ? <p className="text-[11px] text-violet-300">~{money(d.expected_recovery)} expected</p>
            : <p className="text-[11px] text-slate-500">—</p>}
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {d.status === "open" || d.status === "triage" ? <ActionChip action={d.recommended_action} /> : <DenialStatusPill status={d.status} label={d.status_label} />}
          {d.appeal_status === "draft" && d.status === "open" && (
            <span className="rounded-md bg-violet-400/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-violet-200">draft ready</span>
          )}
        </div>
        <div>{d.status === "open" || d.status === "upheld" ? <Deadline days={d.days_left} date={d.deadline} compact /> : <span className="text-xs text-slate-500">{shortDate(d.closed_at ?? d.created_at)}</span>}</div>
        <ArrowRight className="hidden size-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-slate-300 md:block" />
      </Link>
    </motion.li>
  );
}

const TONES = {
  amber: "text-amber-300 bg-amber-400/10 ring-amber-400/20",
  violet: "text-violet-300 bg-violet-400/10 ring-violet-400/20",
  rose: "text-rose-300 bg-rose-400/10 ring-rose-400/20",
  teal: "text-teal-300 bg-teal-400/10 ring-teal-400/20",
};

function Stat({ icon: Icon, tone, label, value, foot, format }: {
  icon: typeof Inbox; tone: keyof typeof TONES; label: string; value: number; foot: React.ReactNode; format?: (v: number) => string;
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

function ImportButton() {
  const input = useRef<HTMLInputElement>(null);
  const queryClient = useQueryClient();
  const [result, setResult] = useState<string | null>(null);
  const upload = useMutation({
    mutationFn: api.importRemittance,
    onSuccess: (r) => {
      queryClient.invalidateQueries({ queryKey: ["denials"] });
      const msg = `${r.matched} claim${r.matched === 1 ? "" : "s"} matched` + (r.unknown_claims.length ? `, ${r.unknown_claims.length} unknown` : "");
      setResult(msg);
      toast.success("835 imported", { description: msg });
    },
    onError: (e) => toast.error(e.message),
  });
  return (
    <div className="flex items-center gap-3">
      <AnimatePresence>{result && <motion.span initial={{ opacity: 0 }} animate={{ opacity: 1 }} className="text-xs text-slate-400">{result}</motion.span>}</AnimatePresence>
      <Button variant="secondary" loading={upload.isPending} onClick={() => input.current?.click()}>
        <FileUp className="size-4" /> Import 835
      </Button>
      <input ref={input} type="file" accept=".835,.txt,.edi,.x12" hidden
        onChange={(e) => { const f = e.target.files?.[0]; if (f) upload.mutate(f); e.target.value = ""; }} />
    </div>
  );
}
