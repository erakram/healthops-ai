"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, BookOpenCheck, Bot, CheckCircle2, CircleDashed, Cpu, FileSearch, HandCoins, Library, Receipt, RefreshCcw,
  Scale, Trash2, XCircle,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { use, useState } from "react";
import { toast } from "sonner";
import { AgentConsole } from "@/components/claim/agent-console";
import { DocumentViewer } from "@/components/claim/document-viewer";
import { AppealEditor, AppealWriting } from "@/components/denials/appeal-editor";
import { ActionChip, CodeBadge, Deadline, DenialStatusPill } from "@/components/denials/bits";
import { RemittanceCard } from "@/components/denials/remittance";
import { Button, Panel, Skeleton, StatusPill } from "@/components/ui";
import { api, type DenialDetail, type Evidence, type Policy } from "@/lib/api";
import { cn, money, shortDate } from "@/lib/utils";

const BUSY = new Set(["triage", "appeal_sent", "resubmitted"]);

export default function DenialPage({ params }: { params: Promise<{ id: string }> }) {
  const id = Number(use(params).id);
  const { data } = useQuery({
    queryKey: ["denial", id],
    queryFn: () => api.denial(id),
    refetchInterval: (q) => {
      const d = q.state.data;
      return d && (BUSY.has(d.status) || d.appeal?.status === "drafting" || d.appeal?.status === "sent") ? 1500 : 20_000;
    },
  });
  if (!data) return <Loading />;
  return <DenialView d={data} />;
}

function DenialView({ d }: { d: DenialDetail }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const [focus, setFocus] = useState<Evidence | null>(null);
  const [policyQuote, setPolicyQuote] = useState<string | null>(null);
  const [writingOff, setWritingOff] = useState(false);
  const [note, setNote] = useState("");
  const canAct = (user?.role === "biller" || user?.role === "admin");
  const actionable = canAct && (d.status === "open" || d.status === "upheld");

  const refresh = (data: DenialDetail) => {
    queryClient.setQueryData(["denial", d.id], data);
    queryClient.invalidateQueries({ queryKey: ["denials"] });
    queryClient.invalidateQueries({ queryKey: ["claim", d.claim_id] });
  };
  const act = useMutation({
    mutationFn: (v: { action: "write_off" | "bill_patient" | "corrected_claim" | "draft_appeal"; note?: string }) =>
      api.denialAction(d.id, v.action, v.note),
    onSuccess: (data, v) => {
      refresh(data);
      setWritingOff(false);
      toast.success({ write_off: "Written off", bill_patient: "Patient statement queued", corrected_claim: "Corrected claim sent to the payer",
        draft_appeal: "The Appeal Agent is drafting" }[v.action]);
    },
    onError: (e) => toast.error(e.message),
  });
  const save = useMutation({
    mutationFn: (p: { source_index: number | null; text: string }[]) => api.saveAppeal(d.appeal!.id, p),
    onSuccess: (data) => { refresh(data); toast.success("Draft saved"); }, onError: (e) => toast.error(e.message),
  });
  const send = useMutation({
    mutationFn: (p: { source_index: number | null; text: string }[]) => api.sendAppeal(d.appeal!.id, p),
    onSuccess: (data) => { refresh(data); toast.success("Appeal sent to the payer"); }, onError: (e) => toast.error(e.message),
  });

  const t = d.triage ?? {};
  const appeal = d.appeal;
  const denied = d.lines.map((l) => l.cpt);
  const tokens = d.agent_runs.reduce((s, r) => s + r.input_tokens + r.output_tokens, 0);
  const working = d.status === "triage" || appeal?.status === "drafting" || d.status === "appeal_sent" || d.status === "resubmitted";

  return (
    <div className="space-y-5">
      {/* Header */}
      <div>
        <Link href="/denials" className="mb-4 inline-flex items-center gap-1.5 text-sm text-slate-400 transition hover:text-white">
          <ArrowLeft className="size-4" /> Denials
        </Link>
        <div className="flex flex-col gap-4 xl:flex-row xl:items-end xl:justify-between">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-3">
              <CodeBadge code={d.code} className="px-3 py-1.5 text-lg" />
              <DenialStatusPill status={d.status} label={d.status_label} />
              <span className="text-xs text-slate-500">{d.group_label} · {d.category_label}</span>
            </div>
            <h1 className="mt-3 max-w-3xl text-xl font-semibold leading-snug text-white md:text-2xl">{d.reason}</h1>
            <p className="mt-2 text-sm text-slate-400">
              <Link href={`/claims/${d.claim_id}`} className="font-mono text-teal-300 hover:underline">{d.claim_number}</Link>
              {" · "}{d.patient_name} · {d.payer_name} · DOS {shortDate(d.service_date)} · CPT {denied.join(", ")}
              {d.remarks.length > 0 && <> · <span title={d.remarks.map((r) => `${r.code}: ${r.text}`).join("\n")} className="cursor-help underline decoration-dotted">{d.remarks.map((r) => r.code).join(" ")}</span></>}
            </p>
          </div>
          <div className="flex items-center gap-6">
            <div className="text-right">
              <p className="text-xs text-slate-500">{d.group === "PR" ? "Patient owes" : "Adjusted"}</p>
              <p className="text-2xl font-semibold tabular-nums text-white">{money(d.amount)}</p>
            </div>
            <StatusPill status={d.claim.status} />
          </div>
        </div>
      </div>

      {/* Recommendation */}
      <Panel className="p-6">
        <div className="pointer-events-none absolute -right-20 -top-24 size-72 rounded-full bg-gradient-to-br from-violet-500/15 to-transparent blur-3xl" />
        <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div>
            <div className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.18em] text-violet-300/80">
              <Bot className="size-4" /> Denial Triage Agent
            </div>
            {d.status === "triage" ? (
              <div className="space-y-2"><Skeleton className="h-8 w-60" /><Skeleton className="h-4 w-full" /><Skeleton className="h-4 w-4/5" /></div>
            ) : (
              <>
                <div className="flex flex-wrap items-center gap-3">
                  <span className="text-sm text-slate-400">Recommends</span>
                  <ActionChip action={d.recommended_action} className="px-3 py-1.5 text-sm" />
                  {d.action_taken && d.action_taken !== d.recommended_action && (
                    <span className="text-xs text-amber-200/80">A person chose: {d.action_taken.replace("_", " ")}</span>
                  )}
                </div>
                <ul className="mt-4 space-y-2">
                  {(t.reasons ?? []).map((r, i) => (
                    <motion.li key={i} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.08 }}
                      className="flex gap-2.5 text-sm text-slate-200">
                      <span className="mt-[7px] size-1.5 shrink-0 rounded-full bg-violet-400" />{r}
                    </motion.li>
                  ))}
                </ul>
              </>
            )}

            {/* Actions */}
            <AnimatePresence mode="wait">
              {actionable && !writingOff && (
                <motion.div key="acts" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} className="mt-5 flex flex-wrap gap-2">
                  {d.recommended_action === "corrected_claim" && t.fix && (
                    <Button variant="primary" loading={act.isPending} onClick={() => act.mutate({ action: "corrected_claim" })}>
                      <RefreshCcw className="size-4" /> Send corrected claim: modifier {t.fix.add_modifier} on {t.fix.cpt}
                    </Button>
                  )}
                  {(d.group === "PR" || d.category === "eligibility") && (
                    <Button variant={d.recommended_action === "bill_patient" ? "primary" : "secondary"} loading={act.isPending}
                      onClick={() => act.mutate({ action: "bill_patient" })}><HandCoins className="size-4" /> Bill the patient</Button>
                  )}
                  {d.group !== "PR" && (!appeal || appeal.status === "failed" || appeal.status === "upheld") && (
                    <Button variant={d.recommended_action === "appeal" && !appeal ? "primary" : "secondary"} loading={act.isPending}
                      onClick={() => act.mutate({ action: "draft_appeal" })}>
                      <Scale className="size-4" /> {appeal ? "Draft a new appeal" : d.recommended_action === "appeal" ? "Draft appeal" : "Draft an appeal anyway"}
                    </Button>
                  )}
                  <Button variant={d.recommended_action === "write_off" ? "danger" : "ghost"} onClick={() => setWritingOff(true)}>
                    <Trash2 className="size-4" /> Write off
                  </Button>
                </motion.div>
              )}
              {actionable && writingOff && (
                <motion.form key="wo" initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }}
                  onSubmit={(e) => { e.preventDefault(); act.mutate({ action: "write_off", note }); }} className="mt-5 flex max-w-xl gap-2">
                  <input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Why is it written off? (goes in the audit trail)"
                    className="min-w-0 flex-1 rounded-xl border border-rose-400/30 bg-white/[0.03] px-3 text-sm text-white outline-none focus:ring-4 focus:ring-rose-400/10" />
                  <Button type="button" variant="ghost" onClick={() => setWritingOff(false)}>Cancel</Button>
                  <Button type="submit" variant="danger" disabled={!note.trim()} loading={act.isPending}>Write off</Button>
                </motion.form>
              )}
            </AnimatePresence>
          </div>

          {/* Odds and deadline */}
          <div className="space-y-4 rounded-2xl border border-white/[0.06] bg-white/[0.02] p-5">
            <div>
              <p className="text-xs text-slate-500">{d.recovered ? "Recovered" : "Expected recovery"}</p>
              <p className={cn("text-3xl font-semibold tabular-nums", d.recovered ? "text-emerald-300" : "text-white")}>
                {money(d.recovered || d.expected_recovery || 0)}
              </p>
            </div>
            {d.win_probability != null && !d.recovered && (
              <div>
                <div className="mb-1.5 flex justify-between text-xs"><span className="text-slate-400">Chance of recovery</span>
                  <span className="tabular-nums text-slate-200">{Math.round(d.win_probability * 100)}%</span></div>
                <div className="h-2 overflow-hidden rounded-full bg-white/[0.06]">
                  <motion.div initial={{ width: 0 }} animate={{ width: `${d.win_probability * 100}%` }} transition={{ duration: 1, ease: [0.16, 1, 0.3, 1] }}
                    className="h-full rounded-full bg-gradient-to-r from-violet-400 via-cyan-400 to-teal-300" />
                </div>
              </div>
            )}
            {(d.status === "open" || d.status === "upheld" || d.status === "triage") ? (
              <div>
                <p className="mb-1.5 text-xs text-slate-500">Act by</p>
                <Deadline days={d.days_left} date={d.deadline} />
              </div>
            ) : (
              <p className="text-xs text-slate-400">{d.status_label}{d.closed_at ? ` on ${shortDate(d.closed_at)}` : ""}</p>
            )}
            {tokens > 0 && <p className="flex items-center gap-1.5 text-[11px] text-slate-500"><Cpu className="size-3" /> {d.agent_runs.length} agent calls · {tokens.toLocaleString()} tokens</p>}
          </div>
        </div>
      </Panel>

      <div className="grid gap-5 xl:grid-cols-[minmax(0,1fr)_minmax(0,520px)]">
        <div className="min-w-0 space-y-5">
          {/* Criteria and policy search */}
          {(t.criteria?.length || t.retrieval?.length) ? (
            <Panel className="p-6">
              <div className="grid gap-6 md:grid-cols-[minmax(0,1fr)_240px]">
                <div>
                  <div className="mb-3 flex items-center gap-2"><BookOpenCheck className="size-4 text-teal-300" />
                    <h2 className="font-medium text-white">Policy criteria{d.policy_id ? ` · ${d.policy_id}` : ""}</h2></div>
                  {t.criteria?.length ? (
                    <ul className="space-y-2.5">
                      {t.criteria.map((c, i) => (
                        <motion.li key={c.id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: 0.1 + i * 0.08 }}
                          className={cn("rounded-xl border p-3", c.met ? "border-emerald-400/20 bg-emerald-400/[0.04]" : "border-rose-400/20 bg-rose-500/[0.04]")}>
                          <div className="flex gap-2.5">
                            {c.met ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-300" /> : <XCircle className="mt-0.5 size-4 shrink-0 text-rose-300" />}
                            <div className="min-w-0">
                              <p className="text-sm text-slate-100"><span className="font-mono text-xs text-slate-500">{c.id}</span> {c.text}</p>
                              {c.met && c.quote && (
                                <button onClick={() => c.evidence && setFocus(c.evidence)}
                                  className={cn("mt-1.5 block max-w-full truncate rounded-md bg-teal-400/10 px-2 py-1 text-left text-xs text-teal-100 ring-1 ring-teal-400/20",
                                    c.evidence && "hover:bg-teal-400/20")}>“{c.quote}”</button>
                              )}
                              {!c.met && c.model_said_met && (
                                <p className="mt-1 text-xs text-amber-200/80">The model said met, but its quote isn&apos;t in the note, so it doesn&apos;t count.</p>
                              )}
                              {!c.met && !c.model_said_met && c.explanation && <p className="mt-1 text-xs text-slate-500">{c.explanation}</p>}
                            </div>
                          </div>
                        </motion.li>
                      ))}
                    </ul>
                  ) : <p className="text-sm text-slate-500">No documentation check needed for this reason code.</p>}
                </div>
                {!!t.retrieval?.length && (
                  <div>
                    <div className="mb-3 flex items-center gap-2"><Library className="size-4 text-violet-300" /><h3 className="text-sm font-medium text-white">Policy search</h3></div>
                    <ul className="space-y-2">
                      {t.retrieval.map((r, i) => {
                        const max = t.retrieval![0].score || 1;
                        const chosen = r.id === d.policy_id;
                        return (
                          <li key={r.id} className={cn("rounded-lg p-2", chosen && "bg-violet-400/[0.08] ring-1 ring-violet-400/25")}>
                            <div className="flex justify-between text-xs"><span className={cn("font-mono", chosen ? "text-violet-200" : "text-slate-400")}>{r.id}</span>
                              <span className="tabular-nums text-slate-500">{r.score.toFixed(1)}</span></div>
                            <p className="truncate text-[11px] text-slate-400">{r.title}</p>
                            <div className="mt-1 h-1 overflow-hidden rounded-full bg-white/[0.05]">
                              <motion.div initial={{ width: 0 }} animate={{ width: `${(r.score / max) * 100}%` }} transition={{ delay: 0.2 + i * 0.1, duration: 0.8 }}
                                className={cn("h-full rounded-full", chosen ? "bg-violet-400" : "bg-slate-500")} />
                            </div>
                          </li>
                        );
                      })}
                    </ul>
                    <p className="mt-2 text-[10px] text-slate-500">BM25 over the payer policy library</p>
                  </div>
                )}
              </div>
            </Panel>
          ) : null}

          {/* Appeal */}
          {appeal && (
            <Panel glow={false} className="p-6">
              <div className="mb-4 flex items-center gap-2"><Scale className="size-4 text-violet-300" /><h2 className="font-medium text-white">Appeal</h2>
                <span className="text-xs text-slate-500">· written by the Appeal Agent, edited and sent by a person</span></div>
              {appeal.status === "drafting" ? <AppealWriting /> : appeal.status === "failed" ? (
                <p className="rounded-xl border border-rose-400/25 bg-rose-500/10 p-4 text-sm text-rose-100">Drafting failed: {appeal.draft.error}</p>
              ) : (
                <AppealEditor appeal={appeal} editable={canAct && d.status === "open"} sending={send.isPending} saving={save.isPending}
                  recovered={d.recovered} onSend={(p) => send.mutate(p)} onSave={(p) => save.mutate(p)} onFocus={setFocus}
                  onPolicy={(q) => setPolicyQuote(q)} />
              )}
            </Panel>
          )}

          {/* Remittances */}
          <Panel glow={false} className="p-6">
            <div className="mb-4 flex items-center gap-2"><Receipt className="size-4 text-sky-300" /><h2 className="font-medium text-white">Remittance (835)</h2>
              <span className="text-xs text-slate-500">· what the payer sent, parsed from the raw file</span></div>
            <div className="space-y-3">
              {d.remittances.map((r) => <RemittanceCard key={r.id} remit={r} highlight={r.id === d.remittance.id ? denied : undefined} />)}
            </div>
          </Panel>

          <AgentConsole events={d.events} running={working} />

          {d.other_denials.length > 0 && (
            <Panel glow={false} className="p-5">
              <p className="mb-2 text-sm font-medium text-white">Other items on this claim</p>
              <div className="space-y-1.5">
                {d.other_denials.map((o) => (
                  <Link key={o.id} href={`/denials/${o.id}`} className="flex items-center gap-3 rounded-lg p-2 text-sm hover:bg-white/[0.03]">
                    <CodeBadge code={o.code} /><span className="flex-1 truncate text-slate-300">{o.reason}</span>
                    <DenialStatusPill status={o.status} label={o.status_label} />
                  </Link>
                ))}
              </div>
            </Panel>
          )}
        </div>

        {/* Evidence */}
        <div className="space-y-5 xl:sticky xl:top-6 xl:self-start">
          <Panel glow={false} className="h-[64vh] min-h-[460px] p-0 [&>div:last-child]:h-full">
            <DocumentViewer documents={d.documents} focus={focus} onSelect={() => setFocus(null)} />
          </Panel>
          {d.policy && <PolicyCard policy={d.policy} quote={policyQuote} criteriaMet={t.criteria} />}
        </div>
      </div>
    </div>
  );
}

function PolicyCard({ policy, quote, criteriaMet }: { policy: Policy; quote: string | null; criteriaMet?: { id: string; met: boolean }[] }) {
  const mark = (text: string) => {
    if (!quote) return text;
    const q = quote.replace(/^[A-Z]\d+:\s*/, "");
    const at = text.toLowerCase().indexOf(q.toLowerCase());
    if (at < 0) return text;
    return <>{text.slice(0, at)}<mark className="rounded bg-violet-400/30 px-0.5 text-violet-50">{text.slice(at, at + q.length)}</mark>{text.slice(at + q.length)}</>;
  };
  return (
    <Panel glow={false} className={cn("p-5 transition-shadow", quote && "shadow-[0_0_0_1px_rgba(167,139,250,0.4),0_0_40px_-10px_rgba(167,139,250,0.5)]")}>
      <div className="mb-2 flex items-center gap-2"><FileSearch className="size-4 text-violet-300" />
        <p className="text-sm font-medium text-white"><span className="font-mono text-violet-200">{policy.id}</span> {policy.title}</p></div>
      <p className="text-xs leading-relaxed text-slate-400">{mark(policy.body)}</p>
      {policy.criteria.length > 0 && (
        <ul className="mt-3 space-y-1.5">
          {policy.criteria.map((c) => {
            const met = criteriaMet?.find((m) => m.id === c.id)?.met;
            return (
              <li key={c.id} className="flex gap-2 text-xs text-slate-300">
                {met ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-300" /> : <CircleDashed className="mt-0.5 size-3.5 shrink-0 text-slate-500" />}
                <span><span className="font-mono text-slate-500">{c.id}</span> {mark(c.text)}</span>
              </li>
            );
          })}
        </ul>
      )}
    </Panel>
  );
}

function Loading() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-10 w-80" />
      <Skeleton className="h-48 rounded-2xl" />
      <div className="grid gap-5 xl:grid-cols-[1fr_520px]"><Skeleton className="h-[480px] rounded-2xl" /><Skeleton className="h-[480px] rounded-2xl" /></div>
    </div>
  );
}
