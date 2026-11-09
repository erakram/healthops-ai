"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { FileText, FlaskConical, Siren, Timer, UploadCloud, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { api, type AuthStatus } from "@/lib/api";
import { cn } from "@/lib/utils";

const TONE: Record<AuthStatus, string> = {
  received: "text-slate-300 bg-slate-400/10 ring-slate-400/20",
  reading: "text-cyan-200 bg-cyan-400/10 ring-cyan-400/25",
  checking: "text-violet-200 bg-violet-400/10 ring-violet-400/25",
  not_required: "text-teal-200 bg-teal-400/10 ring-teal-400/25",
  ineligible: "text-rose-200 bg-rose-400/10 ring-rose-400/25",
  needs_documentation: "text-amber-200 bg-amber-400/10 ring-amber-400/30",
  ready: "text-teal-100 bg-teal-400/15 ring-teal-300/30",
  submitted: "text-sky-200 bg-sky-400/10 ring-sky-400/25",
  approved: "text-emerald-100 bg-emerald-400/15 ring-emerald-300/30",
  pended: "text-orange-200 bg-orange-400/10 ring-orange-400/30",
  denied: "text-rose-200 bg-rose-500/15 ring-rose-400/30",
  withdrawn: "text-slate-400 bg-slate-400/5 ring-slate-400/15",
  failed: "text-rose-200 bg-rose-500/15 ring-rose-400/30",
};
const LIVE = new Set<AuthStatus>(["received", "reading", "checking", "submitted"]);

export function AuthStatusPill({ status, label, className }: { status: AuthStatus; label: string; className?: string }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", TONE[status], className)}>
      {LIVE.has(status) && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
      {label}
    </span>
  );
}

export function UrgentBadge() {
  return (
    <span className="inline-flex items-center gap-1 rounded-md bg-rose-500/15 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-rose-200 ring-1 ring-rose-400/30">
      <Siren className="size-3" /> Urgent
    </span>
  );
}

/** The payer's response clock (CMS: 72 hours urgent, 7 days standard). */
export function PayerClock({ hours, urgent }: { hours: number | null; urgent: boolean }) {
  if (hours == null) return null;
  const total = urgent ? 72 : 168;
  const pct = Math.max(0, Math.min(100, (hours / total) * 100));
  const text = hours < 0 ? `${Math.abs(hours).toFixed(0)}h overdue` : hours >= 48 ? `${(hours / 24).toFixed(1)} days left` : `${hours.toFixed(0)}h left`;
  return (
    <span className="inline-flex items-center gap-2 text-xs tabular-nums text-sky-200" title="Time the payer has left to answer">
      <Timer className="size-3.5" /> {text}
      <span className="h-1 w-14 overflow-hidden rounded-full bg-white/10"><span className="block h-full rounded-full bg-sky-400" style={{ width: `${pct}%` }} /></span>
    </span>
  );
}

const SCENARIO_TAG: Record<string, [string, string]> = {
  approved: ["complete", "text-emerald-300 bg-emerald-400/10"],
  missing_documentation: ["gap + addendum", "text-amber-300 bg-amber-400/10"],
  not_necessary: ["criteria unmet", "text-rose-300 bg-rose-400/10"],
  urgent: ["urgent", "text-rose-300 bg-rose-500/10"],
  not_required: ["no auth", "text-teal-300 bg-teal-400/10"],
  ineligible: ["inactive", "text-slate-300 bg-slate-400/10"],
};

export function NewRequestButton() {
  const [open, setOpen] = useState(false);
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  if (user && !["intake", "biller", "admin"].includes(user.role)) return null;
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)}><FileText className="size-4" /> New request</Button>
      <NewRequestDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

function NewRequestDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [files, setFiles] = useState<File[]>([]);
  const [mounted, setMounted] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => setMounted(true), []);
  const { data: samples } = useQuery({ queryKey: ["auth-samples"], queryFn: api.authSamples, enabled: open });
  const done = (a: { id: number; number: string }) => {
    toast.success(`${a.number} created`, { description: "The agents are reading the order." });
    onClose();
    setFiles([]);
    router.push(`/authorizations/${a.id}`);
  };
  const upload = useMutation({ mutationFn: () => api.createAuthorization(files), onSuccess: done, onError: (e) => toast.error(e.message) });
  const sample = useMutation({ mutationFn: api.loadAuthSample, onSuccess: done, onError: (e) => toast.error(e.message) });
  const shown = (samples ?? []).filter((s, i, all) => all.findIndex((x) => x.scenario === s.scenario) === i);

  if (!mounted) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
          <motion.div initial={{ opacity: 0, y: 30, scale: 0.96 }} animate={{ opacity: 1, y: 0, scale: 1 }} exit={{ opacity: 0, y: 20, scale: 0.97 }}
            transition={{ type: "spring", damping: 28, stiffness: 320 }} className="panel relative max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-3xl">
            <div className="flex items-start justify-between border-b border-white/[0.06] p-6">
              <div>
                <h2 className="text-lg font-semibold text-white">New prior authorization request</h2>
                <p className="text-sm text-slate-400">Upload the service order, the clinical note and the insurance card.</p>
              </div>
              <button onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-white"><X className="size-4" /></button>
            </div>
            <div className="grid gap-6 p-6 md:grid-cols-[1fr_1.1fr]">
              <div>
                <button onClick={() => input.current?.click()}
                  className="grid w-full place-items-center rounded-2xl border-2 border-dashed border-white/10 bg-white/[0.02] p-8 text-center transition hover:border-white/25">
                  <UploadCloud className="mb-2 size-7 text-violet-200" />
                  <p className="text-sm font-medium text-white">Choose PDFs</p>
                  <p className="mt-1 text-xs text-slate-400">Order, note, card. Up to 10 files.</p>
                </button>
                <input ref={input} type="file" accept="application/pdf" multiple hidden
                  onChange={(e) => setFiles([...files, ...Array.from(e.target.files ?? [])].slice(0, 10))} />
                <ul className="mt-3 space-y-1.5">
                  {files.map((f, i) => (
                    <li key={`${f.name}-${i}`} className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-sm">
                      <FileText className="size-4 text-violet-300" /><span className="flex-1 truncate text-slate-200">{f.name}</span>
                      <button onClick={() => setFiles(files.filter((_, j) => j !== i))} className="text-slate-500 hover:text-white"><X className="size-3.5" /></button>
                    </li>
                  ))}
                </ul>
                <Button variant="primary" className="mt-4 w-full" disabled={!files.length} loading={upload.isPending} onClick={() => upload.mutate()}>
                  Check {files.length || ""} document{files.length === 1 ? "" : "s"}
                </Button>
              </div>
              <div>
                <div className="mb-3 flex items-center gap-2"><FlaskConical className="size-4 text-violet-300" /><p className="text-sm font-medium text-white">Or try a synthetic order</p></div>
                <div className="space-y-1.5">
                  {shown.map((s, i) => (
                    <motion.button key={s.case_id} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.04 }}
                      disabled={sample.isPending} onClick={() => sample.mutate(s.case_id)}
                      className="flex w-full items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-left transition hover:border-violet-400/30 hover:bg-violet-400/[0.04]">
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm text-slate-100">{s.title}</p>
                        <p className="text-xs text-slate-500">{s.patient} · {s.files.length} documents{s.has_addendum ? " · addendum available" : ""}</p>
                      </div>
                      {sample.isPending && sample.variables === s.case_id ? <span className="size-4 animate-spin rounded-full border-2 border-violet-300 border-t-transparent" />
                        : <span className={cn("rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", SCENARIO_TAG[s.scenario]?.[1])}>{SCENARIO_TAG[s.scenario]?.[0] ?? s.scenario}</span>}
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
