"use client";

import { AlertTriangle, BadgeCheck, CheckCircle2, Copy, FileWarning, Gavel, ListChecks, Plus, Send, ShieldX, Sparkles, X, XCircle } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import type { Appeal, Citation, Evidence, Paragraph } from "@/lib/api";
import { cn, money } from "@/lib/utils";

type Edit = { source_index: number | null; text: string };

const norm = (t: string) => t.toLowerCase().replace(/[^a-z0-9]/g, "");

/** Same rule as the server: an item counts as cited when a citation from that source quotes it. */
function isCited(item: { source: string; quote: string }, citations: Citation[]) {
  const want = norm(item.quote);
  return citations.some((c) => c.source === item.source && norm(c.quote).length >= Math.min(want.length, 8)
    && (want.includes(norm(c.quote)) || norm(c.quote).includes(want)));
}

function initial(appeal: Appeal): Edit[] {
  const saved = appeal.letter?.paragraphs;
  if (saved?.length) return saved.map((p) => ({ source_index: p.source_index ?? null, text: p.text }));
  return (appeal.draft.paragraphs ?? []).map((p, i) => ({ source_index: i, text: p.text }));
}

export function AppealEditor({ appeal, editable, sending, saving, recovered, onSend, onSave, onFocus, onPolicy }: {
  appeal: Appeal;
  editable: boolean;
  sending: boolean;
  saving: boolean;
  recovered: number;
  onSend: (paragraphs: Edit[]) => void;
  onSave: (paragraphs: Edit[]) => void;
  onFocus: (e: Evidence) => void;
  onPolicy: (quote: string) => void;
}) {
  const draft = appeal.draft;
  const isDraft = appeal.status === "draft";
  const [edits, setEdits] = useState<Edit[]>(() => initial(appeal));
  const [dirty, setDirty] = useState(false);
  useEffect(() => { if (!dirty) setEdits(initial(appeal)); }, [appeal, dirty]);

  if (draft.historical) {
    return (
      <div className="rounded-2xl border border-white/[0.07] bg-white/[0.02] p-5">
        <Outcome appeal={appeal} recovered={recovered} />
        <p className="mt-3 text-xs text-slate-500">Historical demo record: the letter text wasn&apos;t kept for seeded history.</p>
      </div>
    );
  }

  const sourceParagraphs = draft.paragraphs ?? [];
  const sentParagraphs = appeal.letter?.paragraphs;
  const shown: (Paragraph & { idx: number })[] = isDraft
    ? edits.map((e, idx) => {
      const src = e.source_index != null ? sourceParagraphs[e.source_index] : undefined;
      return { kind: src?.kind ?? "argument", text: e.text, citations: src?.citations ?? [], idx,
        edited: !src || src.text !== e.text, source_index: e.source_index };
    })
    : (sentParagraphs?.length ? sentParagraphs : sourceParagraphs).map((p, idx) => ({ ...p, idx }));
  const stats = draft.stats;
  const header = draft.header;
  const change = (next: Edit[]) => { setEdits(next); setDirty(true); };

  return (
    <div className="space-y-4">
      {(appeal.status === "overturned" || appeal.status === "upheld") && <Outcome appeal={appeal} recovered={recovered} />}
      {appeal.status === "sent" && (
        <div className="flex items-center gap-3 rounded-2xl border border-violet-400/25 bg-violet-500/[0.07] p-4">
          <span className="relative flex size-2.5"><span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-violet-400" /><span className="relative size-2.5 rounded-full bg-violet-400" /></span>
          <p className="text-sm text-violet-100">Sent. Waiting for the payer&apos;s appeal reviewer…</p>
        </div>
      )}

      {stats && (
        <div className="flex flex-wrap gap-2 text-xs">
          <span className="inline-flex items-center gap-1.5 rounded-lg bg-emerald-400/10 px-2 py-1 text-emerald-200 ring-1 ring-emerald-400/25">
            <BadgeCheck className="size-3.5" /> {stats.verified_citations}/{stats.citations} citations found in the record
          </span>
          {stats.removed_paragraphs > 0 && (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-rose-400/10 px-2 py-1 text-rose-200 ring-1 ring-rose-400/25">
              <ShieldX className="size-3.5" /> {stats.removed_paragraphs} unsupported paragraph{stats.removed_paragraphs > 1 ? "s" : ""} removed
            </span>
          )}
          {draft.attachments?.length ? (
            <span className="inline-flex items-center gap-1.5 rounded-lg bg-white/[0.05] px-2 py-1 text-slate-300 ring-1 ring-white/10">
              Enclosures: {draft.attachments.join(", ")}
            </span>
          ) : null}
        </div>
      )}

      {!!draft.checklist?.length && (() => {
        const all = shown.flatMap((p) => p.citations);
        const items = draft.checklist!.map((c) => ({ ...c, ok: isCited(c, all) }));
        const missing = items.filter((i) => !i.ok);
        return (
          <div className={cn("rounded-2xl border p-4", missing.length ? "border-amber-400/30 bg-amber-400/[0.06]" : "border-emerald-400/20 bg-emerald-400/[0.04]")}>
            <p className="mb-2 flex items-center gap-2 text-sm text-white">
              {missing.length ? <AlertTriangle className="size-4 text-amber-300" /> : <ListChecks className="size-4 text-emerald-300" />}
              Evidence the payer will look for: {items.length - missing.length}/{items.length} cited
            </p>
            <ul className="space-y-1">
              {items.map((i, k) => (
                <li key={k} className="flex gap-2 text-xs">
                  {i.ok ? <CheckCircle2 className="mt-0.5 size-3.5 shrink-0 text-emerald-300" /> : <XCircle className="mt-0.5 size-3.5 shrink-0 text-amber-300" />}
                  <span className={i.ok ? "text-slate-400" : "text-amber-100"}><span className="font-mono">{i.id}</span> “{i.quote}”</span>
                </li>
              ))}
            </ul>
            {missing.length > 0 && isDraft && <p className="mt-2 text-[11px] text-amber-200/70">Missing evidence usually means the payer upholds. Ask for a new draft, or add it before sending.</p>}
          </div>
        );
      })()}

      {/* The letter, on paper */}
      <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }}
        className="relative overflow-hidden rounded-2xl bg-[#f7f6f2] p-6 text-[13.5px] leading-relaxed text-slate-800 shadow-[0_30px_60px_-25px_rgba(0,0,0,0.9)] ring-1 ring-white/20 md:p-8">
        <div className="pointer-events-none absolute inset-x-0 top-0 h-1 bg-gradient-to-r from-teal-400 via-cyan-400 to-violet-400" />
        {header && (
          <div className="mb-5 space-y-3 text-[12.5px]">
            <div className="flex justify-between gap-4 text-slate-600"><span>{header.from}</span><span>{header.date}</span></div>
            <p className="text-slate-600">{header.to}</p>
            <p className="font-semibold text-slate-900">{header.subject}</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-0.5 rounded-lg bg-slate-900/[0.04] p-3 text-[12px]">
              {header.reference.map(([k, v]) => (
                <div key={k} className="contents"><dt className="text-slate-500">{k}</dt><dd className="font-medium text-slate-800">{v}</dd></div>
              ))}
            </dl>
            <p>{header.salutation}</p>
          </div>
        )}

        <div className="space-y-4">
          <AnimatePresence initial={false}>
            {shown.map((p) => (
              <motion.div key={`${p.idx}-${p.source_index ?? "new"}`} layout initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, x: 20 }} className="group relative">
                {isDraft && editable ? (
                  <AutoTextarea value={p.text} onChange={(text) => change(edits.map((e, i) => (i === p.idx ? { ...e, text } : e)))}
                    className={cn(p.edited && "bg-amber-100/60")} />
                ) : (
                  <p className="whitespace-pre-wrap">{p.text}</p>
                )}
                {isDraft && editable && (
                  <button onClick={() => change(edits.filter((_, i) => i !== p.idx))} title="Remove paragraph"
                    className="absolute -right-3 -top-2 hidden rounded-full bg-slate-800 p-1 text-white shadow group-hover:block"><X className="size-3" /></button>
                )}
                <div className="mt-1.5 flex flex-wrap gap-1.5">
                  {p.citations.map((c, j) => <CitationChip key={j} c={c} onFocus={onFocus} onPolicy={onPolicy} />)}
                  {p.kind === "argument" && !p.citations.length && (
                    <span className="inline-flex items-center gap-1 rounded-md bg-amber-200/70 px-1.5 py-0.5 text-[11px] text-amber-900">
                      <FileWarning className="size-3" /> Added by a person: not backed by a citation
                    </span>
                  )}
                </div>
              </motion.div>
            ))}
          </AnimatePresence>
          {isDraft && editable && (
            <button onClick={() => change([...edits.slice(0, -1), { source_index: null, text: "" }, ...edits.slice(-1)])}
              className="flex items-center gap-1.5 rounded-lg border border-dashed border-slate-400/60 px-3 py-1.5 text-xs text-slate-500 transition hover:border-slate-600 hover:text-slate-800">
              <Plus className="size-3.5" /> Add a paragraph
            </button>
          )}
        </div>

        {header && (
          <div className="mt-6 text-[12.5px]">
            <p>{header.closing}</p>
            <p className="mt-6 font-medium text-slate-900">{header.signature}</p>
          </div>
        )}
      </motion.div>

      {/* What verification removed */}
      {!!draft.removed?.length && (
        <details className="group rounded-2xl border border-rose-400/20 bg-rose-500/[0.04] p-4">
          <summary className="flex cursor-pointer list-none items-center gap-2 text-sm text-rose-100">
            <ShieldX className="size-4 text-rose-300" /> Removed by verification ({draft.removed.length})
            <span className="text-xs text-rose-200/50">· the model wrote these, but the record doesn&apos;t back them</span>
          </summary>
          <ul className="mt-3 space-y-2">
            {draft.removed.map((r, i) => (
              <li key={i} className="rounded-xl bg-black/20 p-3">
                <p className="text-sm text-slate-300 line-through decoration-rose-400/60">{r.text}</p>
                <p className="mt-1 text-xs text-rose-200/80">{r.reason}</p>
              </li>
            ))}
          </ul>
        </details>
      )}

      {isDraft && editable && (
        <div className="flex flex-wrap items-center justify-end gap-2">
          {dirty && <span className="mr-auto text-xs text-amber-200/80">Edited. Citations stay attached to the agent&apos;s paragraphs.</span>}
          <Button variant="ghost" onClick={() => {
            navigator.clipboard.writeText(shown.map((p) => p.text).join("\n\n"));
            toast.success("Letter body copied");
          }}><Copy className="size-4" /> Copy</Button>
          {dirty && <Button variant="secondary" loading={saving} onClick={() => { onSave(edits.filter((e) => e.text.trim())); setDirty(false); }}>Save draft</Button>}
          <Button variant="primary" loading={sending} disabled={!edits.some((e) => e.text.trim())}
            onClick={() => onSend(edits.filter((e) => e.text.trim()))}>
            <Send className="size-4" /> Send appeal to payer
          </Button>
        </div>
      )}
    </div>
  );
}

function CitationChip({ c, onFocus, onPolicy }: { c: Citation; onFocus: (e: Evidence) => void; onPolicy: (q: string) => void }) {
  const policy = c.source === "policy";
  return (
    <button
      onClick={() => (c.evidence ? onFocus(c.evidence) : policy && onPolicy(c.quote))}
      title={c.quote}
      className={cn("inline-flex max-w-full items-center gap-1 rounded-md px-1.5 py-0.5 text-left text-[11px] ring-1 transition",
        policy ? "bg-violet-100 text-violet-900 ring-violet-300 hover:bg-violet-200" : "bg-teal-100 text-teal-900 ring-teal-300 hover:bg-teal-200")}>
      <CheckCircle2 className="size-3 shrink-0" />
      <span className="shrink-0 font-semibold">{c.label}:</span>
      <span className="truncate">“{c.quote.length > 70 ? `${c.quote.slice(0, 70)}…` : c.quote}”</span>
    </button>
  );
}

function AutoTextarea({ value, onChange, className }: { value: string; onChange: (v: string) => void; className?: string }) {
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    const el = ref.current;
    if (el) { el.style.height = "auto"; el.style.height = `${el.scrollHeight}px`; }
  }, [value]);
  return (
    <textarea ref={ref} value={value} onChange={(e) => onChange(e.target.value)} rows={2}
      placeholder="Write a paragraph…"
      className={cn("block w-full resize-none rounded-md bg-transparent p-1 -m-1 outline-none transition focus:bg-white focus:ring-2 focus:ring-teal-400/50", className)} />
  );
}

function Outcome({ appeal, recovered }: { appeal: Appeal; recovered: number }) {
  const won = appeal.status === "overturned";
  return (
    <motion.div initial={{ opacity: 0, scale: 0.97 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", damping: 20 }}
      className={cn("relative overflow-hidden rounded-2xl border p-5",
        won ? "border-emerald-300/30 bg-gradient-to-r from-emerald-500/15 via-teal-500/10 to-transparent" : "border-rose-400/25 bg-gradient-to-r from-rose-500/10 to-transparent")}>
      {won && <motion.div className="pointer-events-none absolute -left-10 -top-10 size-40 rounded-full bg-emerald-400/30 blur-3xl"
        animate={{ scale: [1, 1.3, 1], opacity: [0.6, 0.9, 0.6] }} transition={{ duration: 3, repeat: Infinity }} />}
      <div className="relative flex items-start gap-4">
        <div className={cn("grid size-11 shrink-0 place-items-center rounded-xl ring-1", won ? "bg-emerald-400/20 text-emerald-200 ring-emerald-300/40" : "bg-rose-500/15 text-rose-200 ring-rose-400/30")}>
          {won ? <Gavel className="size-5" /> : <XCircle className="size-5" />}
        </div>
        <div className="min-w-0">
          <p className={cn("font-semibold", won ? "text-emerald-100" : "text-rose-100")}>
            {won ? <>Appeal overturned{recovered ? <> · <span className="tabular-nums">{money(recovered)}</span> recovered</> : null}</> : "Appeal upheld by the payer"}
          </p>
          <p className="mt-1 text-sm text-slate-300">{appeal.outcome_reason}</p>
        </div>
      </div>
    </motion.div>
  );
}

export function AppealWriting() {
  return (
    <div className="relative overflow-hidden rounded-2xl border border-violet-400/20 bg-violet-500/[0.05] p-6">
      <div className="absolute inset-x-0 top-0 h-px overflow-hidden"><div className="h-full w-1/3 animate-beam bg-gradient-to-r from-transparent via-violet-300 to-transparent" /></div>
      <div className="mb-4 flex items-center gap-2 text-sm text-violet-100">
        <Sparkles className="size-4 animate-pulse text-violet-300" /> The Appeal Agent is drafting and checking every quote against the record…
      </div>
      <div className="space-y-2.5">
        {[92, 85, 97, 60, 88, 74].map((w, i) => (
          <motion.div key={i} className="skeleton h-3 rounded" style={{ width: `${w}%` }}
            initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: i * 0.12 }} />
        ))}
      </div>
    </div>
  );
}
