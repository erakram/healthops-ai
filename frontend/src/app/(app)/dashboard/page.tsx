"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, ArrowRight, Bot, CircleDollarSign, Clock3, Cpu, Gavel, Percent, Scale, Send, ShieldAlert, Sparkles, Timer } from "lucide-react";
import { motion } from "motion/react";
import Link from "next/link";
import { Area, AreaChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ActorDot } from "@/components/actor-dot";
import { NewClaimButton } from "@/components/upload-dialog";
import { AnimatedNumber, PageHeader, Panel, Skeleton, STATUS_META, stagger } from "@/components/ui";
import { api, type ClaimStatus, type Dashboard } from "@/lib/api";
import { cn, money, timeAgo } from "@/lib/utils";

function greeting() {
  const h = new Date().getHours();
  return h < 12 ? "Good morning" : h < 18 ? "Good afternoon" : "Good evening";
}

export default function DashboardPage() {
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data } = useQuery({ queryKey: ["dashboard"], queryFn: api.dashboard, refetchInterval: 20_000 });

  return (
    <>
      <PageHeader
        eyebrow="Command center"
        title={<>{greeting()}{user ? `, ${user.name.split(" ")[0]}` : ""}</>}
        description="Everything the claim agents did, what needs a decision, and how the pipeline is performing."
        actions={<NewClaimButton />}
      />
      {!data ? <DashboardSkeleton /> : <DashboardBody data={data} />}
    </>
  );
}

function DashboardBody({ data }: { data: Dashboard }) {
  const k = data.kpis;
  const highRisk = data.by_risk.high ?? 0;
  return (
    <motion.div variants={stagger.container} initial="hidden" animate="show" className="space-y-5">
      {/* KPIs */}
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Kpi icon={AlertTriangle} tone="amber" label="Waiting for a decision" value={k.awaiting_review}
          foot={<><span className="text-rose-300">{money(k.value_at_risk, true)}</span> in high-risk claims</>} href="/claims?view=review" />
        <Kpi icon={Send} tone="teal" label="Submitted to payers" value={k.submitted}
          foot={<><span className="text-teal-200">{money(k.billed_submitted, true)}</span> billed</>} />
        <CleanRate value={k.clean_rate} />
        <Kpi icon={Timer} tone="violet" label="Upload to review-ready" value={k.avg_processing_seconds ?? 0}
          format={(v) => (k.avg_processing_seconds == null ? "—" : `${v.toFixed(1)}s`)}
          foot={k.avg_processing_seconds == null ? "Process a claim to measure" : <>average across {k.ai_calls} agent calls</>} />
      </div>

      <Revenue data={data} />

      <div className="grid gap-5 xl:grid-cols-3">
        {/* Throughput */}
        <motion.div variants={stagger.item} className="xl:col-span-2">
          <Panel className="h-full p-6">
            <div className="mb-4 flex items-center justify-between">
              <div>
                <h3 className="font-medium text-white">Claim throughput</h3>
                <p className="text-xs text-slate-400">Received vs. submitted, last 14 days</p>
              </div>
              <div className="flex gap-4 text-xs">
                <span className="flex items-center gap-1.5 text-slate-300"><span className="size-2 rounded-full bg-cyan-400" /> Received</span>
                <span className="flex items-center gap-1.5 text-slate-300"><span className="size-2 rounded-full bg-teal-300" /> Submitted</span>
              </div>
            </div>
            <div className="h-64">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={data.throughput} margin={{ left: -20, right: 8, top: 8 }}>
                  <defs>
                    <linearGradient id="gReceived" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#22d3ee" stopOpacity={0.35} />
                      <stop offset="100%" stopColor="#22d3ee" stopOpacity={0} />
                    </linearGradient>
                    <linearGradient id="gSubmitted" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#5eead4" stopOpacity={0.45} />
                      <stop offset="100%" stopColor="#5eead4" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid stroke="rgba(255,255,255,0.05)" vertical={false} />
                  <XAxis dataKey="day" tickFormatter={(d) => new Date(`${d}T00:00:00`).toLocaleDateString("en-US", { month: "short", day: "numeric" })}
                    tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} interval={1} />
                  <YAxis allowDecimals={false} tick={{ fill: "#64748b", fontSize: 11 }} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: "rgba(10,15,28,0.95)", border: "1px solid rgba(255,255,255,0.1)", borderRadius: 12, fontSize: 12 }}
                    labelStyle={{ color: "#e2e8f0" }} cursor={{ stroke: "rgba(255,255,255,0.15)" }} />
                  <Area type="monotoneX" dataKey="received" name="Received" stroke="#22d3ee" strokeWidth={2} fill="url(#gReceived)" animationDuration={1400} />
                  <Area type="monotoneX" dataKey="submitted" name="Submitted" stroke="#5eead4" strokeWidth={2} fill="url(#gSubmitted)" animationDuration={1600} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          </Panel>
        </motion.div>

        {/* Pipeline */}
        <motion.div variants={stagger.item}>
          <Panel className="h-full p-6">
            <h3 className="font-medium text-white">Pipeline</h3>
            <p className="mb-5 text-xs text-slate-400">{k.total_claims} claims · {highRisk} high risk</p>
            <div className="space-y-3.5">
              {(["validating", "needs_review", "ready", "submitted", "paid", "denied", "appealed", "rejected", "failed"] as ClaimStatus[]).map((s, i) => {
                const n = data.by_status[s] ?? 0;
                const pct = k.total_claims ? (n / k.total_claims) * 100 : 0;
                return (
                  <div key={s}>
                    <div className="mb-1.5 flex justify-between text-xs">
                      <span className="text-slate-300">{STATUS_META[s].label}</span>
                      <span className="tabular-nums text-slate-400">{n}</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-white/[0.05]">
                      <motion.div initial={{ width: 0 }} animate={{ width: `${Math.max(pct, n ? 2 : 0)}%` }}
                        transition={{ delay: 0.3 + i * 0.07, duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
                        className={cn("h-full rounded-full", STATUS_META[s].dot)} />
                    </div>
                  </div>
                );
              })}
            </div>
          </Panel>
        </motion.div>
      </div>

      <div className="grid gap-5 lg:grid-cols-2 xl:grid-cols-3">
        {/* Top findings */}
        <motion.div variants={stagger.item}>
          <Panel className="h-full p-6">
            <div className="mb-4 flex items-center gap-2">
              <ShieldAlert className="size-4 text-orange-300" />
              <h3 className="font-medium text-white">What the agents catch most</h3>
            </div>
            <div className="space-y-3">
              {data.top_findings.map((f, i) => {
                const max = data.top_findings[0]?.count || 1;
                return (
                  <div key={f.code} className="group">
                    <div className="mb-1 flex justify-between text-xs">
                      <span className="font-mono text-slate-300">{f.code}</span>
                      <span className="tabular-nums text-slate-400">{f.count}</span>
                    </div>
                    <div className="h-2 overflow-hidden rounded-full bg-white/[0.04]">
                      <motion.div initial={{ width: 0 }} animate={{ width: `${(f.count / max) * 100}%` }}
                        transition={{ delay: 0.4 + i * 0.06, duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
                        className="h-full rounded-full bg-gradient-to-r from-orange-400/80 via-rose-400/70 to-violet-400/70" />
                    </div>
                  </div>
                );
              })}
              {!data.top_findings.length && <p className="text-sm text-slate-500">No findings yet.</p>}
            </div>
          </Panel>
        </motion.div>

        {/* Agents */}
        <motion.div variants={stagger.item}>
          <Panel className="h-full p-6">
            <div className="mb-4 flex items-center gap-2">
              <Bot className="size-4 text-cyan-300" />
              <h3 className="font-medium text-white">Agent performance</h3>
            </div>
            {data.agents.length ? (
              <div className="space-y-3">
                {data.agents.map((a) => (
                  <div key={a.agent} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                    <div className="flex items-center justify-between">
                      <p className="text-sm font-medium text-white">{a.agent}</p>
                      <span className="flex items-center gap-1 text-[11px] text-emerald-300"><span className="size-1.5 rounded-full bg-emerald-400" /> active</span>
                    </div>
                    <div className="mt-3 grid grid-cols-3 gap-2 text-center">
                      <Stat icon={Activity} label="calls" value={a.calls.toLocaleString()} />
                      <Stat icon={Cpu} label="tokens" value={a.tokens >= 1000 ? `${(a.tokens / 1000).toFixed(1)}k` : String(a.tokens)} />
                      <Stat icon={Clock3} label="avg" value={`${(a.avg_latency_ms / 1000).toFixed(1)}s`} />
                    </div>
                  </div>
                ))}
              </div>
            ) : (
              <div className="grid h-40 place-items-center rounded-xl border border-dashed border-white/10 text-center">
                <div>
                  <Sparkles className="mx-auto mb-2 size-5 text-violet-300" />
                  <p className="text-sm text-slate-300">No live agent runs yet</p>
                  <p className="text-xs text-slate-500">Load a sample packet to watch the agents work.</p>
                </div>
              </div>
            )}
          </Panel>
        </motion.div>

        {/* Activity */}
        <motion.div variants={stagger.item} className="lg:col-span-2 xl:col-span-1">
          <Panel className="h-full p-6">
            <div className="mb-4 flex items-center gap-2">
              <span className="relative flex size-2"><span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-teal-400" /><span className="relative size-2 rounded-full bg-teal-400" /></span>
              <h3 className="font-medium text-white">Live activity</h3>
            </div>
            <ol className="relative max-h-[330px] space-y-3 overflow-y-auto pr-1">
              {data.recent.map((e, i) => (
                <motion.li key={`${e.claim_number}-${e.at}-${i}`} initial={{ opacity: 0, x: 8 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.03 }}>
                  <Link href={`/claims/${e.claim_id}`} className="flex gap-3 rounded-lg p-1.5 transition hover:bg-white/[0.03]">
                    <ActorDot actor={e.actor} />
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-[13px] text-slate-200">{e.message}</p>
                      <p className="text-[11px] text-slate-500"><span className="font-mono text-slate-400">{e.claim_number}</span> · {e.actor} · {timeAgo(e.at)}</p>
                    </div>
                  </Link>
                </motion.li>
              ))}
            </ol>
          </Panel>
        </motion.div>
      </div>
    </motion.div>
  );
}

function Revenue({ data }: { data: Dashboard }) {
  const r = data.revenue;
  const maxReason = r.by_reason[0]?.amount || 1;
  return (
    <motion.div variants={stagger.item}>
      <Panel className="p-6">
        <div className="pointer-events-none absolute -left-24 -bottom-24 size-72 rounded-full bg-gradient-to-tr from-emerald-500/10 to-transparent blur-3xl" />
        <div className="mb-5 flex flex-wrap items-center justify-between gap-2">
          <div>
            <div className="flex items-center gap-2"><CircleDollarSign className="size-4 text-emerald-300" /><h3 className="font-medium text-white">Revenue recovery</h3></div>
            <p className="text-xs text-slate-400">After submission: what payers paid, denied, and what the denial agents got back</p>
          </div>
          <Link href="/denials" className="group flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm text-teal-200 ring-1 ring-teal-400/25 transition hover:bg-teal-400/10">
            Work the denials <ArrowRight className="size-4 transition group-hover:translate-x-0.5" />
          </Link>
        </div>
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)]">
          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            {[
              { icon: CircleDollarSign, label: "Paid by payers", value: r.paid, fmt: (v: number) => money(v, true), foot: `of ${money(r.billed, true)} billed`, tone: "text-emerald-300" },
              { icon: Percent, label: "Denial rate", value: r.denial_rate ?? 0, fmt: (v: number) => (r.denial_rate == null ? "—" : `${v.toFixed(1)}%`), foot: `${money(r.denied_amount, true)} denied`, tone: "text-orange-300" },
              { icon: Scale, label: "Open denials", value: r.open_denials, fmt: undefined, foot: `${money(r.expected_recovery, true)} expected back`, tone: "text-violet-300" },
              { icon: Gavel, label: "Recovered", value: r.recovered, fmt: (v: number) => money(v, true), foot: r.appeal_win_rate == null ? "no appeals decided yet" : `${r.appeal_win_rate}% of appeals won`, tone: "text-teal-300" },
            ].map((s) => (
              <div key={s.label} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
                <s.icon className={cn("mb-2 size-4", s.tone)} />
                <AnimatedNumber value={s.value} format={s.fmt} className="block text-2xl font-semibold tracking-tight text-white" />
                <p className="text-xs text-slate-400">{s.label}</p>
                <p className="mt-1 text-[11px] text-slate-500">{s.foot}</p>
              </div>
            ))}
          </div>
          <div>
            <p className="mb-2 text-xs font-medium text-slate-400">Denied dollars by reason code</p>
            <div className="space-y-2.5">
              {r.by_reason.slice(0, 6).map((row, i) => (
                <div key={row.code}>
                  <div className="mb-1 flex justify-between text-xs">
                    <span className="font-mono text-slate-300">{row.code} <span className="font-sans text-slate-500">· {row.category.replace("_", " ")}</span></span>
                    <span className="tabular-nums text-slate-400">{money(row.amount, true)} · {row.count}</span>
                  </div>
                  <div className="h-2 overflow-hidden rounded-full bg-white/[0.04]">
                    <motion.div initial={{ width: 0 }} animate={{ width: `${(row.amount / maxReason) * 100}%` }}
                      transition={{ delay: 0.3 + i * 0.07, duration: 0.9, ease: [0.16, 1, 0.3, 1] }}
                      className="h-full rounded-full bg-gradient-to-r from-orange-400/80 to-violet-400/70" />
                  </div>
                </div>
              ))}
              {!r.by_reason.length && <p className="text-sm text-slate-500">No denials yet.</p>}
            </div>
          </div>
        </div>
      </Panel>
    </motion.div>
  );
}

const TONES = {
  amber: { icon: "text-amber-300 bg-amber-400/10 ring-amber-400/20", glow: "from-amber-400/15" },
  teal: { icon: "text-teal-300 bg-teal-400/10 ring-teal-400/20", glow: "from-teal-400/15" },
  violet: { icon: "text-violet-300 bg-violet-400/10 ring-violet-400/20", glow: "from-violet-400/15" },
  cyan: { icon: "text-cyan-300 bg-cyan-400/10 ring-cyan-400/20", glow: "from-cyan-400/15" },
};

function Kpi({ icon: Icon, tone, label, value, foot, format, href }: {
  icon: typeof Activity; tone: keyof typeof TONES; label: string; value: number; foot: React.ReactNode;
  format?: (v: number) => string; href?: string;
}) {
  const body = (
    <Panel className="group h-full p-5 transition-transform duration-300 hover:-translate-y-0.5">
      <div className={cn("pointer-events-none absolute -right-10 -top-10 size-32 rounded-full bg-gradient-to-br to-transparent blur-2xl", TONES[tone].glow)} />
      <div className="flex items-start justify-between">
        <p className="text-sm text-slate-400">{label}</p>
        <div className={cn("grid size-9 place-items-center rounded-xl ring-1", TONES[tone].icon)}><Icon className="size-[18px]" /></div>
      </div>
      <AnimatedNumber value={value} format={format} className="mt-3 block text-3xl font-semibold tracking-tight text-white" />
      <p className="mt-2 text-xs text-slate-400">{foot}</p>
    </Panel>
  );
  return <motion.div variants={stagger.item}>{href ? <Link href={href}>{body}</Link> : body}</motion.div>;
}

function CleanRate({ value }: { value: number | null }) {
  const r = 26;
  const c = 2 * Math.PI * r;
  return (
    <motion.div variants={stagger.item}>
      <Panel className="h-full p-5">
        <div className="flex items-center justify-between gap-4">
          <div>
            <p className="text-sm text-slate-400">First-pass clean rate</p>
            <AnimatedNumber value={value ?? 0} format={(v) => (value == null ? "—" : `${v.toFixed(0)}%`)} className="mt-3 block text-3xl font-semibold tracking-tight text-white" />
            <p className="mt-2 text-xs text-slate-400">claims with no issues to fix</p>
          </div>
          <svg viewBox="0 0 64 64" className="size-20 -rotate-90">
            <defs>
              <linearGradient id="ring" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0%" stopColor="#2dd4bf" /><stop offset="100%" stopColor="#a78bfa" />
              </linearGradient>
            </defs>
            <circle cx="32" cy="32" r={r} fill="none" stroke="rgba(255,255,255,0.06)" strokeWidth="6" />
            <motion.circle cx="32" cy="32" r={r} fill="none" stroke="url(#ring)" strokeWidth="6" strokeLinecap="round"
              strokeDasharray={c} initial={{ strokeDashoffset: c }} animate={{ strokeDashoffset: c - (c * (value ?? 0)) / 100 }}
              transition={{ duration: 1.4, delay: 0.3, ease: [0.16, 1, 0.3, 1] }} />
          </svg>
        </div>
      </Panel>
    </motion.div>
  );
}

function Stat({ icon: Icon, label, value }: { icon: typeof Activity; label: string; value: string }) {
  return (
    <div className="rounded-lg bg-white/[0.03] py-2">
      <Icon className="mx-auto mb-1 size-3.5 text-slate-500" />
      <p className="text-sm font-semibold tabular-nums text-white">{value}</p>
      <p className="text-[10px] uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-5">
      <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-36 rounded-2xl" />)}
      </div>
      <div className="grid gap-5 xl:grid-cols-3">
        <Skeleton className="h-80 rounded-2xl xl:col-span-2" />
        <Skeleton className="h-80 rounded-2xl" />
      </div>
    </div>
  );
}
