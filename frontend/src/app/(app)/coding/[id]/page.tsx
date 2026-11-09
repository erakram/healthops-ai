"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, ArrowDown, ArrowLeft, ArrowUp, Bot, Check, CheckCircle2, Cpu, FileSearch, Gauge, Inbox, Minus, Plus,
  Receipt, RotateCcw, Search, Stethoscope, Tags, UserCheck, Wand2, X,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { AgentConsole } from "@/components/claim/agent-console";
import { DocumentViewer } from "@/components/claim/document-viewer";
import { EncStatusPill } from "@/components/coding/bits";
import { Button, Panel, Skeleton } from "@/components/ui";
import { api, type CodingEdit, type CptLine, type EncounterDetail, type Evidence } from "@/lib/api";
import { cn, shortDate } from "@/lib/utils";

const WORKING = new Set(["received", "reading", "coding"]);

export default function EncounterPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number(use(params).id);
  const { data } = useQuery({
    queryKey: ["encounter", id], queryFn: () => api.encounter(id),
    refetchInterval: (q) => (q.state.data && WORKING.has(q.state.data.status) ? 1500 : 20_000),
  });
  if (!data) return <Loading />;
  return <Workbench key={`${data.id}-${data.status}`} e={data} />;
}

type Dx = { code: string; description: string };

function Workbench({ e }: { e: EncounterDetail }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data: cptCatalog } = useQuery({ queryKey: ["cpt"], queryFn: api.cptList, staleTime: Infinity });
  const s = e.suggestion ?? {};
  const fromFinal = !!e.final?.icd;
  const [icd, setIcd] = useState<Dx[]>(() => (fromFinal ? e.final.icd! : (s.diagnoses ?? []).map((d) => ({ code: d.code, description: d.description }))));
  const [cpt, setCpt] = useState<CptLine[]>(() => (fromFinal ? e.final.cpt! : (s.procedures ?? [])).map((p) => ({ code: p.code, modifiers: p.modifiers, units: p.units })));
  const [reason, setReason] = useState("");
  const [focus, setFocus] = useState<Evidence | null>(null);
  const editable = e.status === "review" && (user?.role === "biller" || user?.role === "admin");
  const cptName = useMemo(() => Object.fromEntries((cptCatalog ?? []).map((c) => [c.code, c.description])), [cptCatalog]);

  const check = useQuery({
    queryKey: ["code-check", e.id, JSON.stringify(icd.map((d) => d.code)), JSON.stringify(cpt)],
    queryFn: () => api.checkCodes(e.id, icd.map((d) => d.code), cpt), enabled: editable, staleTime: 5_000,
  });
  const edits: CodingEdit[] = editable ? check.data?.edits ?? s.edits ?? [] : e.final?.edits ?? s.edits ?? [];
  const changes = editable ? check.data?.changes ?? [] : e.final?.changes ?? [];

  const refresh = (d: EncounterDetail) => { queryClient.setQueryData(["encounter", e.id], d); queryClient.invalidateQueries({ queryKey: ["encounters"] }); };
  const finalize = useMutation({ mutationFn: () => api.finalizeCodes(e.id, icd.map((d) => d.code), cpt, reason || undefined),
    onSuccess: (d) => { refresh(d); toast.success("Codes approved"); }, onError: (err) => toast.error(err.message) });
  const claim = useMutation({ mutationFn: () => api.createClaimFromEncounter(e.id),
    onSuccess: (d) => { refresh(d); toast.success(`Claim ${d.claim_number} generated`, { description: "It's entering the claim pipeline now." }); }, onError: (err) => toast.error(err.message) });
  const reopen = useMutation({ mutationFn: () => api.reopenEncounter(e.id), onSuccess: refresh, onError: (err) => toast.error(err.message) });
  const retry = useMutation({ mutationFn: () => api.retryEncounter(e.id), onSuccess: refresh, onError: (err) => toast.error(err.message) });

  const applyFix = (edit: CodingEdit) => {
    const fix = edit.fix ?? {};
    if (fix.replace) setIcd(icd.map((d) => (d.code === edit.target ? { code: fix.replace!, description: "" } : d)));
    if (fix.remove) setCpt(cpt.filter((p) => p.code !== fix.remove));
    if (fix.add_modifier) setCpt(cpt.map((p) => (p.code === edit.target ? { ...p, modifiers: [...p.modifiers, fix.add_modifier!] } : p)));
    if (fix.units) setCpt(cpt.map((p) => (p.code === edit.target ? { ...p, units: fix.units! } : p)));
  };
  const evidenceFor = (code: string) => s.diagnoses?.find((d) => d.code === code) ?? s.procedures?.find((p) => p.code === code);
  const tokens = e.agent_runs.reduce((n, r) => n + r.input_tokens + r.output_tokens, 0);

  return (
    <div className="space-y-5">
      <div>
        <Link href="/coding" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-white"><ArrowLeft className="size-4" /> Coding</Link>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div>
            <div className="flex flex-wrap items-center gap-3">
              <h1 className="font-mono text-3xl font-semibold tracking-tight text-white">{e.number}</h1>
              <EncStatusPill status={e.status} label={e.status_label} />
            </div>
            <p className="mt-2 text-sm text-slate-400">{e.patient_name ?? "Reading the note…"} · {e.provider_name ?? "—"} · {e.payer_name ?? "—"} · DOS {shortDate(e.service_date)}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            {e.status === "failed" && <Button loading={retry.isPending} onClick={() => retry.mutate()}><RotateCcw className="size-4" /> Retry</Button>}
            {e.status === "coded" && user?.role !== "intake" && <>
              <Button variant="ghost" loading={reopen.isPending} onClick={() => reopen.mutate()}>Reopen</Button>
              <Button variant="primary" loading={claim.isPending} onClick={() => claim.mutate()}><Receipt className="size-4" /> Generate claim</Button>
            </>}
            {e.claim_id && <Link href={`/claims/${e.claim_id}`}><Button variant="primary"><Receipt className="size-4" /> Open the claim</Button></Link>}
          </div>
        </div>
      </div>

      <Panel glow={false} className="p-5"><Steps status={e.status} /></Panel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,500px)]">
        <div className="min-w-0 space-y-5">
          {WORKING.has(e.status) && !s.diagnoses ? (
            <Panel className="p-6"><div className="mb-3 flex items-center gap-2 text-sm text-violet-200"><Bot className="size-4 animate-pulse" /> The Coding Agent is reading the note and searching the code sets…</div>
              <div className="space-y-2">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-14 rounded-xl" />)}</div></Panel>
          ) : (
            <>
              {s.em && <EmCard e={e} onFocus={setFocus} />}

              {/* Diagnoses */}
              <Panel className="p-6">
                <div className="mb-4 flex items-center gap-2"><Stethoscope className="size-4 text-cyan-300" /><h2 className="font-medium text-white">Diagnoses</h2>
                  <span className="text-xs text-slate-500">· ICD-10-CM FY2026, 74,719 billable codes · first is primary</span></div>
                <ul className="space-y-2">
                  <AnimatePresence initial={false}>
                    {icd.map((d, i) => {
                      const ev = evidenceFor(d.code);
                      const sug = s.diagnoses?.find((x) => x.code === d.code);
                      return (
                        <motion.li key={d.code} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0, x: 20 }}
                          className={cn("rounded-xl border p-3", sug ? "border-white/[0.07] bg-white/[0.02]" : "border-amber-400/25 bg-amber-400/[0.04]")}>
                          <div className="flex items-start gap-3">
                            <span className={cn("mt-0.5 grid size-6 shrink-0 place-items-center rounded-md text-[11px] font-semibold", i === 0 ? "bg-cyan-400/20 text-cyan-100" : "bg-white/[0.06] text-slate-400")}>{i + 1}</span>
                            <div className="min-w-0 flex-1">
                              <p className="text-sm"><span className="font-mono font-semibold text-white">{d.code}</span> <span className="text-slate-300">{d.description || sug?.description}</span>
                                {!sug && <span className="ml-2 text-[11px] text-amber-200">added by coder</span>}</p>
                              {ev?.quote && (
                                <button onClick={() => ev.evidence && setFocus(ev.evidence)} className="mt-1.5 block max-w-full truncate rounded-md bg-teal-400/10 px-2 py-1 text-left text-xs text-teal-100 ring-1 ring-teal-400/20 hover:bg-teal-400/20">“{ev.quote}”</button>
                              )}
                              {editable && sug && sug.candidates.length > 1 && (
                                <div className="mt-2 flex flex-wrap items-center gap-1 text-[11px] text-slate-500">Other matches:
                                  {sug.candidates.filter((c) => c.code !== d.code).slice(0, 4).map((c) => (
                                    <button key={c.code} title={c.description} onClick={() => setIcd(icd.map((x) => (x.code === d.code ? { code: c.code, description: c.description } : x)))}
                                      className="rounded bg-white/[0.04] px-1.5 py-0.5 font-mono text-slate-300 ring-1 ring-white/10 hover:bg-white/[0.1]">{c.code}</button>
                                  ))}
                                </div>
                              )}
                            </div>
                            {editable && <div className="flex shrink-0 gap-0.5">
                              <IconBtn label="Move up" disabled={i === 0} onClick={() => setIcd(swap(icd, i, i - 1))}><ArrowUp className="size-3.5" /></IconBtn>
                              <IconBtn label="Move down" disabled={i === icd.length - 1} onClick={() => setIcd(swap(icd, i, i + 1))}><ArrowDown className="size-3.5" /></IconBtn>
                              <IconBtn label="Remove" onClick={() => setIcd(icd.filter((x) => x.code !== d.code))}><X className="size-3.5" /></IconBtn>
                            </div>}
                          </div>
                        </motion.li>
                      );
                    })}
                  </AnimatePresence>
                </ul>
                {editable && <IcdSearch onPick={(d) => !icd.some((x) => x.code === d.code) && setIcd([...icd, d])} />}
              </Panel>

              {/* Procedures */}
              <Panel className="p-6">
                <div className="mb-4 flex items-center gap-2"><Tags className="size-4 text-violet-300" /><h2 className="font-medium text-white">Procedures and visit</h2>
                  <span className="text-xs text-slate-500">· CPT (curated list: CPT is AMA-licensed)</span></div>
                <ul className="space-y-2">
                  {cpt.map((p, i) => {
                    const ev = evidenceFor(p.code);
                    return (
                      <li key={`${p.code}-${i}`} className="rounded-xl border border-white/[0.07] bg-white/[0.02] p-3">
                        <div className="flex flex-wrap items-center gap-3">
                          <span className="font-mono font-semibold text-white">{p.code}</span>
                          <span className="min-w-0 flex-1 truncate text-sm text-slate-300">{cptName[p.code] ?? ""}</span>
                          <div className="flex items-center gap-1">
                            {["25", "59", "RT", "LT"].map((m) => (
                              <button key={m} disabled={!editable} onClick={() => setCpt(cpt.map((x, j) => (j === i ? { ...x, modifiers: x.modifiers.includes(m) ? x.modifiers.filter((y) => y !== m) : [...x.modifiers, m] } : x)))}
                                className={cn("rounded-md px-1.5 py-0.5 font-mono text-[11px] ring-1 transition", p.modifiers.includes(m) ? "bg-violet-400/20 text-violet-100 ring-violet-300/40" : "text-slate-500 ring-white/10 hover:text-slate-200", !editable && !p.modifiers.includes(m) && "hidden")}>{m}</button>
                            ))}
                          </div>
                          <div className="flex items-center gap-1 text-xs text-slate-300">
                            {editable && <IconBtn label="Fewer units" disabled={p.units <= 1} onClick={() => setCpt(cpt.map((x, j) => (j === i ? { ...x, units: x.units - 1 } : x)))}><Minus className="size-3" /></IconBtn>}
                            <span className="tabular-nums">×{p.units}</span>
                            {editable && <IconBtn label="More units" onClick={() => setCpt(cpt.map((x, j) => (j === i ? { ...x, units: x.units + 1 } : x)))}><Plus className="size-3" /></IconBtn>}
                          </div>
                          {editable && <IconBtn label="Remove" onClick={() => setCpt(cpt.filter((_, j) => j !== i))}><X className="size-3.5" /></IconBtn>}
                        </div>
                        {ev?.quote && <button onClick={() => ev.evidence && setFocus(ev.evidence)} className="mt-1.5 block max-w-full truncate rounded-md bg-teal-400/10 px-2 py-1 text-left text-xs text-teal-100 ring-1 ring-teal-400/20 hover:bg-teal-400/20">“{ev.quote}”</button>}
                      </li>
                    );
                  })}
                </ul>
                {editable && cptCatalog && (
                  <select value="" onChange={(ev) => ev.target.value && setCpt([...cpt, { code: ev.target.value, modifiers: [], units: 1 }])}
                    className="mt-3 w-full rounded-xl border border-white/10 bg-ink-900 px-3 py-2 text-sm text-slate-300 outline-none">
                    <option value="">+ Add a procedure or visit code…</option>
                    {cptCatalog.map((c) => <option key={c.code} value={c.code}>{c.code} {c.description}</option>)}
                  </select>
                )}
              </Panel>

              {/* Checks */}
              <Panel glow={false} className={cn("p-6", edits.length ? "border-amber-400/20" : "border-emerald-400/20")}>
                <div className="mb-3 flex items-center gap-2">
                  {edits.length ? <AlertTriangle className="size-4 text-amber-300" /> : <CheckCircle2 className="size-4 text-emerald-300" />}
                  <h2 className="font-medium text-white">Coding checks</h2>
                  <span className="text-xs text-slate-500">· rules, re-run on every change{check.isFetching ? " …" : ""}</span>
                </div>
                {edits.length === 0 ? <p className="text-sm text-emerald-200/80">No problems: codes are billable, specific, consistent with the note, and modifiers are right.</p> : (
                  <ul className="space-y-2">
                    {edits.map((ed, i) => (
                      <li key={i} className={cn("flex items-start gap-3 rounded-xl p-3", ed.severity === "high" ? "bg-rose-500/[0.07]" : ed.severity === "medium" ? "bg-amber-400/[0.07]" : "bg-white/[0.03]")}>
                        <span className={cn("mt-0.5 rounded px-1.5 py-0.5 text-[10px] font-semibold uppercase", ed.severity === "high" ? "bg-rose-500/20 text-rose-200" : ed.severity === "medium" ? "bg-amber-400/20 text-amber-100" : "bg-white/10 text-slate-300")}>{ed.severity}</span>
                        <div className="min-w-0 flex-1"><p className="text-sm text-white">{ed.title}</p><p className="text-xs text-slate-400">{ed.detail}</p></div>
                        {editable && ed.fix && <Button variant="secondary" className="shrink-0 px-2.5 py-1 text-xs" onClick={() => applyFix(ed)}><Wand2 className="size-3.5" /> Fix</Button>}
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>

              {/* Approve */}
              {editable && (
                <Panel glow={false} className="p-5">
                  {changes.length > 0 && (
                    <div className="mb-3">
                      <p className="mb-2 text-sm text-slate-300">You changed the agent&apos;s codes: {changes.map((c) => `${c.change} ${c.code}`).join(", ")}. Why? (kept as a test case)</p>
                      <input value={reason} onChange={(ev) => setReason(ev.target.value)} placeholder="e.g. note documents left knee, not right"
                        className="w-full rounded-xl border border-white/15 bg-white/[0.03] px-3 py-2 text-sm text-white outline-none focus:ring-4 focus:ring-cyan-400/10" />
                    </div>
                  )}
                  <div className="flex items-center justify-between gap-3">
                    <p className="text-xs text-slate-500">{changes.length ? `${changes.length} change(s) from the agent` : "Unchanged from the Coding Agent"} · {edits.filter((x) => x.severity === "high").length} high-severity check(s)</p>
                    <Button variant="primary" loading={finalize.isPending} disabled={!icd.length || !cpt.length || (changes.length > 0 && !reason.trim())} onClick={() => finalize.mutate()}>
                      <Check className="size-4" /> Approve codes
                    </Button>
                  </div>
                </Panel>
              )}
              {e.final?.icd && !editable && (
                <p className="flex items-center gap-2 text-sm text-slate-400"><UserCheck className="size-4 text-teal-300" /> Approved by {e.coded_by} {e.final.agreed ? "without changes" : `with ${e.final.changes?.length} change(s): ${e.final.reason}`}</p>
              )}
            </>
          )}
          <AgentConsole events={e.events} running={WORKING.has(e.status)} />
          {tokens > 0 && <p className="flex items-center gap-1.5 text-xs text-slate-500"><Cpu className="size-3.5" /> {e.agent_runs.length} agent calls · {tokens.toLocaleString()} tokens
            {s.stats && <> · {s.stats.grounded}/{s.stats.quotes} codes pinned to the note{s.stats.rejected_choices ? ` · ${s.stats.rejected_choices} off-list answer(s) rejected` : ""}</>}</p>}
        </div>

        <div className="xl:sticky xl:top-6 xl:self-start">
          <Panel glow={false} className="h-[78vh] min-h-[520px] p-0 [&>div:last-child]:h-full">
            {e.documents.length ? <DocumentViewer documents={e.documents} focus={focus} onSelect={() => setFocus(null)} /> : <div className="grid h-full place-items-center text-sm text-slate-500">No documents</div>}
          </Panel>
        </div>
      </div>
    </div>
  );
}

function swap<T>(list: T[], a: number, b: number): T[] {
  const next = [...list];
  [next[a], next[b]] = [next[b], next[a]];
  return next;
}

function IconBtn({ label, onClick, disabled, children }: { label: string; onClick: () => void; disabled?: boolean; children: React.ReactNode }) {
  return <button title={label} aria-label={label} disabled={disabled} onClick={onClick} className="rounded-md p-1 text-slate-400 transition hover:bg-white/10 hover:text-white disabled:opacity-30">{children}</button>;
}

function IcdSearch({ onPick }: { onPick: (d: { code: string; description: string }) => void }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const { data, isFetching } = useQuery({ queryKey: ["icd-search", debounced], queryFn: () => api.searchIcd10(debounced), enabled: debounced.length >= 2 });
  return (
    <div className="relative mt-3">
      <div className="flex items-center gap-2 rounded-xl border border-white/10 bg-white/[0.03] px-3">
        <Search className="size-4 text-slate-500" />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Add a diagnosis: search 74,719 codes by words or code…"
          className="min-w-0 flex-1 bg-transparent py-2 text-sm text-white outline-none" />
        {isFetching && <span className="size-3.5 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" />}
      </div>
      <AnimatePresence>
        {debounced.length >= 2 && data && data.length > 0 && q && (
          <motion.ul initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
            className="absolute z-20 mt-1 max-h-72 w-full overflow-y-auto rounded-xl border border-white/10 bg-ink-900 p-1 shadow-2xl">
            {data.map((h) => (
              <li key={h.code}><button onClick={() => { onPick({ code: h.code, description: h.description }); setQ(""); }}
                className="flex w-full gap-3 rounded-lg px-2.5 py-1.5 text-left text-sm hover:bg-white/[0.06]">
                <span className="w-20 shrink-0 font-mono text-cyan-200">{h.code}</span><span className="text-slate-300">{h.description}</span></button></li>
            ))}
          </motion.ul>
        )}
      </AnimatePresence>
    </div>
  );
}

function EmCard({ e, onFocus }: { e: EncounterDetail; onFocus: (ev: Evidence) => void }) {
  const em = e.suggestion.em!;
  const mdm = e.suggestion.documented?.mdm;
  const time = e.suggestion.documented?.time;
  const rank: Record<string, number> = { minimal: 1, straightforward: 1, low: 2, moderate: 3, high: 4 };
  return (
    <Panel className="p-6">
      <div className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-gradient-to-br from-cyan-500/15 to-transparent blur-3xl" />
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-cyan-300/80"><Gauge className="size-4" /> Visit level · 2021 E/M rules</div>
          <p className="mt-2 font-mono text-4xl font-semibold text-white">{em.code}</p>
          <p className="mt-1 text-sm text-slate-300">{em.patient_status} patient · {em.mdm_level} medical decision making · billed by {em.basis}</p>
        </div>
        <p className="max-w-sm text-xs text-slate-400">{em.explanation} The model only reports what&apos;s documented; this table picks the level, so it can&apos;t upcode.</p>
      </div>
      <div className="mt-5 grid gap-3 sm:grid-cols-4">
        {(["problems", "data", "risk"] as const).map((k, i) => {
          const el = mdm?.[k] ?? {};
          return (
            <motion.div key={k} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.08 }} className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
              <p className="text-[11px] uppercase tracking-wider text-slate-500">{k}</p>
              <p className="mt-1 text-sm font-medium capitalize text-white">{el.level ?? "minimal"}</p>
              <div className="mt-2 flex gap-0.5">{[1, 2, 3, 4].map((n) => <span key={n} className={cn("h-1 flex-1 rounded-full", n <= (rank[el.level ?? "minimal"] ?? 1) ? "bg-cyan-400" : "bg-white/10")} />)}</div>
              {el.quote && <button onClick={() => el.evidence && onFocus(el.evidence)} title={el.quote} className="mt-2 line-clamp-2 text-left text-[11px] text-teal-200/80 hover:text-teal-100">“{el.quote}”</button>}
            </motion.div>
          );
        })}
        <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
          <p className="text-[11px] uppercase tracking-wider text-slate-500">Time</p>
          <p className="mt-1 text-sm font-medium text-white">{time?.value ? `${time.value} min` : "Not documented"}</p>
          {em.time_code && <p className="mt-2 text-[11px] text-slate-400">supports {em.time_code}</p>}
          {time?.evidence && <button onClick={() => onFocus(time.evidence!)} className="mt-1 text-[11px] text-teal-200/80 hover:text-teal-100">Show in note</button>}
        </div>
      </div>
    </Panel>
  );
}

const STEPS = [{ label: "Received", icon: Inbox }, { label: "Reading", icon: FileSearch }, { label: "Coding", icon: Bot },
  { label: "Coder review", icon: UserCheck }, { label: "Coded", icon: CheckCircle2 }, { label: "Claim", icon: Receipt }];
const POS: Record<string, number> = { received: 0, reading: 1, coding: 2, review: 3, coded: 4, billed: 5, failed: 1 };

function Steps({ status }: { status: string }) {
  const current = POS[status];
  const done = status === "billed";
  return (
    <div className="relative px-1 py-2">
      <div className="absolute left-[8.33%] right-[8.33%] top-[26px] h-[2px] rounded-full bg-white/[0.06]">
        <motion.div className={cn("h-full rounded-full", status === "failed" ? "bg-rose-400/70" : "bg-gradient-to-r from-teal-400 via-cyan-400 to-violet-400")}
          initial={{ width: 0 }} animate={{ width: `${(current / (STEPS.length - 1)) * 100}%` }} transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }} />
      </div>
      <div className="relative grid grid-cols-6">
        {STEPS.map((st, i) => {
          const isDone = i < current || (done && i === current);
          const here = i === current && !done;
          const Icon = isDone ? Check : st.icon;
          return (
            <div key={st.label} className="flex flex-col items-center gap-2">
              <div className={cn("relative grid size-[38px] place-items-center rounded-xl ring-1",
                status === "failed" && here ? "bg-rose-500/20 text-rose-200 ring-rose-400/40"
                  : isDone ? "bg-gradient-to-br from-teal-400 to-cyan-500 text-ink-950 ring-transparent shadow-[0_0_20px_-4px_rgba(45,212,191,0.8)]"
                  : here ? "bg-ink-800 text-white ring-teal-300/60" : "bg-ink-900 text-slate-500 ring-white/10")}>
                {here && status !== "failed" && <span className="absolute inset-0 animate-pulse-ring rounded-xl ring-2 ring-teal-300/50" />}
                <Icon className="size-[17px]" strokeWidth={isDone ? 3 : 2} />
              </div>
              <span className={cn("text-center text-[11px] font-medium", here || isDone ? "text-slate-200" : "text-slate-500")}>{st.label}</span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function Loading() {
  return <div className="space-y-5"><Skeleton className="h-10 w-72" /><Skeleton className="h-24 rounded-2xl" /><Skeleton className="h-[480px] rounded-2xl" /></div>;
}
