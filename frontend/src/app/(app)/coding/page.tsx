"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, FileText, FlaskConical, Handshake, ListChecks, Sparkles, Tags, UploadCloud, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { AnimatedNumber, Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { EncStatusPill } from "@/components/coding/bits";
import { api, type EncounterSummary } from "@/lib/api";
import { cn, shortDate } from "@/lib/utils";

const VIEWS = [{ key: "open", label: "To code" }, { key: "done", label: "Coded" }] as const;

export default function CodingPage() {
  const [view, setView] = useState<(typeof VIEWS)[number]["key"]>("open");
  const [open, setOpen] = useState(false);
  const { data: summary } = useQuery({ queryKey: ["encounters", "summary"], queryFn: api.encountersSummary, refetchInterval: 20_000 });
  const { data: rows } = useQuery({ queryKey: ["encounters", view], queryFn: () => api.encounters(view), refetchInterval: 10_000 });
  return (
    <>
      <PageHeader eyebrow="After the visit" title="Medical coding"
        description="The Coding Agent reads each visit note, searches the full ICD-10-CM code set and the CPT list, and suggests codes with the sentence that supports each one. The visit level comes from the 2021 E/M rules, not the model. A coder approves every code."
        actions={<Button variant="primary" onClick={() => setOpen(true)}><FileText className="size-4" /> New encounter</Button>} />
      <NewEncounterDialog open={open} onClose={() => setOpen(false)} />

      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Stat icon={ListChecks} tone="amber" label="Waiting for a coder" value={summary?.review ?? 0} foot="suggestions ready to review" />
        <Stat icon={Handshake} tone="teal" label="Coder agreed with the agent" value={summary?.agreement_rate ?? 0}
          format={(v) => (summary?.agreement_rate == null ? "—" : `${v.toFixed(0)}%`)} foot={`${summary?.coded ?? 0} encounters coded`} />
        <Stat icon={Tags} tone="cyan" label="Claims generated" value={summary?.billed ?? 0} foot="sent into the claim pipeline" />
        <Stat icon={Sparkles} tone="violet" label="Coding checks raised" value={summary?.edits_raised ?? 0} foot="before anything reached a payer" />
      </motion.div>

      <Panel glow={false} className="p-0">
        <div className="flex border-b border-white/[0.06] p-3">
          <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
            {VIEWS.map((v) => (
              <button key={v.key} onClick={() => setView(v.key)} className={cn("relative rounded-lg px-3 py-1.5 text-sm", view === v.key ? "text-white" : "text-slate-400 hover:text-slate-200")}>
                {view === v.key && <motion.span layoutId="enc-view" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                <span className="relative">{v.label}</span>
              </button>
            ))}
          </div>
        </div>
        {!rows ? <div className="space-y-2 p-4">{Array.from({ length: 4 }).map((_, i) => <Skeleton key={i} className="h-16 rounded-xl" />)}</div>
          : rows.length === 0 ? (
            <div className="grid place-items-center py-20 text-center"><Sparkles className="mb-3 size-6 text-cyan-300" />
              <p className="text-slate-200">Nothing here</p><p className="text-sm text-slate-500">Start a new encounter from a synthetic visit note.</p></div>
          ) : (
            <motion.ul key={view} variants={stagger.container} initial="hidden" animate="show" className="divide-y divide-white/[0.05]">
              {rows.map((e) => <Row key={e.id} e={e} />)}
            </motion.ul>
          )}
      </Panel>
    </>
  );
}

function Row({ e }: { e: EncounterSummary }) {
  return (
    <motion.li variants={stagger.item}>
      <Link href={`/coding/${e.id}`} className="group grid gap-3 px-4 py-4 transition-colors hover:bg-white/[0.025] md:grid-cols-[120px_minmax(0,1fr)_minmax(0,1.2fr)_150px_24px] md:items-center">
        <p className="font-mono text-sm text-white">{e.number}</p>
        <div className="min-w-0">
          <p className="truncate text-sm text-slate-100">{e.patient_name ?? "Reading the note…"}</p>
          <p className="truncate text-xs text-slate-500">{e.provider_name ?? "—"} · {e.payer_name ?? "—"} · DOS {shortDate(e.service_date)}</p>
        </div>
        <div className="flex flex-wrap gap-1">
          {e.codes.slice(0, 6).map((c) => <span key={c} className="rounded-md bg-white/[0.05] px-1.5 py-0.5 font-mono text-[11px] text-slate-300 ring-1 ring-white/10">{c}</span>)}
          {e.edits > 0 && e.status === "review" && <span className="rounded-md bg-amber-400/10 px-1.5 py-0.5 text-[11px] text-amber-200">{e.edits} check{e.edits > 1 ? "s" : ""}</span>}
        </div>
        <div className="flex flex-col items-start gap-1">
          <EncStatusPill status={e.status} label={e.status_label} />
          {e.agreed === false && <span className="text-[11px] text-slate-500">{e.changes} coder change{e.changes > 1 ? "s" : ""}</span>}
        </div>
        <ArrowRight className="hidden size-4 text-slate-600 transition group-hover:translate-x-0.5 group-hover:text-slate-300 md:block" />
      </Link>
    </motion.li>
  );
}

const TONES = { amber: "text-amber-300 bg-amber-400/10 ring-amber-400/20", teal: "text-teal-300 bg-teal-400/10 ring-teal-400/20",
  cyan: "text-cyan-300 bg-cyan-400/10 ring-cyan-400/20", violet: "text-violet-300 bg-violet-400/10 ring-violet-400/20" };

function Stat({ icon: Icon, tone, label, value, foot, format }: { icon: typeof Tags; tone: keyof typeof TONES; label: string; value: number; foot: string; format?: (v: number) => string }) {
  return (
    <motion.div variants={stagger.item}>
      <Panel className="h-full p-5">
        <div className="flex items-start justify-between"><p className="text-sm text-slate-400">{label}</p>
          <div className={cn("grid size-9 place-items-center rounded-xl ring-1", TONES[tone])}><Icon className="size-[18px]" /></div></div>
        <AnimatedNumber value={value} format={format} className="mt-3 block text-3xl font-semibold tracking-tight text-white" />
        <p className="mt-2 text-xs text-slate-400">{foot}</p>
      </Panel>
    </motion.div>
  );
}

function NewEncounterDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [mounted, setMounted] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setMounted(true), []);
  const { data: samples } = useQuery({ queryKey: ["coding-samples"], queryFn: api.codingSamples, enabled: open });
  const done = (e: { id: number; number: string }) => { toast.success(`${e.number} created`, { description: "The Coding Agent is reading the note." }); onClose(); setFiles([]); router.push(`/coding/${e.id}`); };
  const upload = useMutation({ mutationFn: () => api.createEncounter(files), onSuccess: done, onError: (e) => toast.error(e.message) });
  const sample = useMutation({ mutationFn: api.loadCodingSample, onSuccess: done, onError: (e) => toast.error(e.message) });
  const shown = (samples ?? []).filter((s, i, all) => all.findIndex((x) => x.scenario === s.scenario) === i);
  if (!mounted) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
          <motion.div initial={{ opacity: 0, y: 30, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 20 }} transition={{ type: "spring", damping: 28, stiffness: 320 }}
            className="panel relative max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-3xl">
            <div className="flex items-start justify-between border-b border-white/[0.06] p-6">
              <div><h2 className="text-lg font-semibold text-white">New encounter to code</h2><p className="text-sm text-slate-400">Upload the visit note and the insurance card.</p></div>
              <button onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-white"><X className="size-4" /></button>
            </div>
            <div className="grid gap-6 p-6 md:grid-cols-[1fr_1.2fr]">
              <div>
                <button onClick={() => input.current?.click()} className="grid w-full place-items-center rounded-2xl border-2 border-dashed border-white/10 bg-white/[0.02] p-8 text-center hover:border-white/25">
                  <UploadCloud className="mb-2 size-7 text-cyan-200" /><p className="text-sm font-medium text-white">Choose PDFs</p></button>
                <input ref={input} type="file" accept="application/pdf" multiple hidden onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])].slice(0, 5))} />
                <ul className="mt-3 space-y-1.5">{files.map((f, i) => <li key={i} className="truncate rounded-lg bg-white/[0.03] px-3 py-2 text-sm text-slate-200">{f.name}</li>)}</ul>
                <Button variant="primary" className="mt-4 w-full" disabled={!files.length} loading={upload.isPending} onClick={() => upload.mutate()}>Code {files.length || ""} document{files.length === 1 ? "" : "s"}</Button>
              </div>
              <div>
                <div className="mb-3 flex items-center gap-2"><FlaskConical className="size-4 text-cyan-300" /><p className="text-sm font-medium text-white">Or try a synthetic visit</p></div>
                <div className="space-y-1.5">
                  {shown.map((s, i) => (
                    <motion.button key={s.case_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }} disabled={sample.isPending}
                      onClick={() => sample.mutate(s.case_id)} className="flex w-full items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-left transition hover:border-cyan-400/30 hover:bg-cyan-400/[0.04]">
                      <div className="min-w-0 flex-1"><p className="truncate text-sm text-slate-100">{s.title}</p><p className="text-xs text-slate-500">{s.patient}</p></div>
                      {sample.isPending && sample.variables === s.case_id && <span className="size-4 animate-spin rounded-full border-2 border-cyan-300 border-t-transparent" />}
                    </motion.button>
                  ))}
                </div>
              </div>
            </div>
          </motion.div>
        </div>
      )}
    </AnimatePresence>,
    document.body,
  );
}
