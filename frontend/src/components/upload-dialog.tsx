"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileText, FlaskConical, Plus, UploadCloud, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

const SCENARIO_TONE: Record<string, string> = {
  clean: "text-emerald-300 bg-emerald-400/10",
  note_mismatch: "text-violet-300 bg-violet-400/10",
  prompt_injection: "text-rose-300 bg-rose-400/10",
};

export function NewClaimButton({ className }: { className?: string }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <Button variant="primary" onClick={() => setOpen(true)} className={className}>
        <Plus className="size-4" /> New claim
      </Button>
      <UploadDialog open={open} onClose={() => setOpen(false)} />
    </>
  );
}

export function UploadDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [dragging, setDragging] = useState(false);
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const { data: samples } = useQuery({ queryKey: ["samples"], queryFn: api.samples, enabled: open });

  const done = (claim: { id: number; number: string }) => {
    queryClient.invalidateQueries({ queryKey: ["claims"] });
    toast.success(`${claim.number} created`, { description: "The agents are reading the documents now." });
    onClose();
    setFiles([]);
    router.push(`/claims/${claim.id}`);
  };
  const upload = useMutation({ mutationFn: () => api.upload(files), onSuccess: done, onError: (e) => toast.error(e.message) });
  const sample = useMutation({ mutationFn: api.loadSample, onSuccess: done, onError: (e) => toast.error(e.message) });

  const addFiles = (list: FileList | null) => {
    if (!list) return;
    const pdfs = Array.from(list).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    if (pdfs.length < list.length) toast.warning("Only PDF files are accepted");
    setFiles((prev) => [...prev, ...pdfs].slice(0, 10));
  };

  // A curated, varied subset: one sample per scenario.
  const shown = (samples ?? []).filter((s, i, all) => all.findIndex((x) => x.scenario === s.scenario) === i);

  if (!mounted) return null;
  return createPortal(
    <AnimatePresence>
      {open && (
        <div className="fixed inset-0 z-50 grid place-items-center p-4">
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
            className="absolute inset-0 bg-black/70 backdrop-blur-sm" onClick={onClose} />
          <motion.div
            initial={{ opacity: 0, y: 30, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.97 }}
            transition={{ type: "spring", damping: 28, stiffness: 320 }}
            className="panel relative max-h-[90vh] w-full max-w-3xl overflow-y-auto rounded-3xl"
          >
            <div className="flex items-start justify-between border-b border-white/[0.06] p-6">
              <div>
                <h2 className="text-lg font-semibold text-white">New claim</h2>
                <p className="text-sm text-slate-400">Upload a claim packet. The Document Agent works out what each file is.</p>
              </div>
              <button onClick={onClose} className="rounded-lg p-2 text-slate-400 hover:bg-white/5 hover:text-white"><X className="size-4" /></button>
            </div>

            <div className="grid gap-6 p-6 md:grid-cols-[1.1fr_1fr]">
              <div>
                <motion.div
                  onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
                  onDragLeave={() => setDragging(false)}
                  onDrop={(e) => { e.preventDefault(); setDragging(false); addFiles(e.dataTransfer.files); }}
                  onClick={() => input.current?.click()}
                  animate={{ scale: dragging ? 1.02 : 1 }}
                  className={cn(
                    "relative grid cursor-pointer place-items-center overflow-hidden rounded-2xl border-2 border-dashed p-8 text-center transition-colors",
                    dragging ? "border-teal-300/70 bg-teal-400/10" : "border-white/10 bg-white/[0.02] hover:border-white/25",
                  )}
                >
                  <motion.div animate={{ y: dragging ? -6 : 0 }} className="animate-float">
                    <div className="mx-auto mb-3 grid size-14 place-items-center rounded-2xl bg-gradient-to-br from-teal-400/20 to-violet-500/20 ring-1 ring-white/10">
                      <UploadCloud className="size-7 text-teal-200" />
                    </div>
                  </motion.div>
                  <p className="text-sm font-medium text-white">Drop PDFs here or click to browse</p>
                  <p className="mt-1 text-xs text-slate-400">Claim form, invoice, insurance card, notes. Up to 10 files.</p>
                  <input ref={input} type="file" accept="application/pdf" multiple hidden onChange={(e) => addFiles(e.target.files)} />
                </motion.div>

                <ul className="mt-3 space-y-1.5">
                  <AnimatePresence initial={false}>
                    {files.map((f, i) => (
                      <motion.li key={`${f.name}-${i}`} layout initial={{ opacity: 0, x: -10 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 10 }}
                        className="flex items-center gap-2 rounded-lg bg-white/[0.03] px-3 py-2 text-sm">
                        <FileText className="size-4 text-cyan-300" />
                        <span className="flex-1 truncate text-slate-200">{f.name}</span>
                        <span className="text-xs text-slate-500">{(f.size / 1024).toFixed(0)} KB</span>
                        <button onClick={() => setFiles(files.filter((_, j) => j !== i))} className="text-slate-500 hover:text-white"><X className="size-3.5" /></button>
                      </motion.li>
                    ))}
                  </AnimatePresence>
                </ul>
                <Button variant="primary" className="mt-4 w-full" disabled={!files.length} loading={upload.isPending} onClick={() => upload.mutate()}>
                  Process {files.length || ""} document{files.length === 1 ? "" : "s"}
                </Button>
              </div>

              <div>
                <div className="mb-3 flex items-center gap-2">
                  <FlaskConical className="size-4 text-violet-300" />
                  <p className="text-sm font-medium text-white">Or try a synthetic packet</p>
                </div>
                <div className="max-h-[400px] space-y-4 overflow-y-auto pr-1">
                  {([["intake", "Problems the agents catch before submission"], ["after_submission", "Clean claims the payer will answer (denials & appeals)"]] as const).map(([stage, heading]) => (
                    <div key={stage}>
                      <p className="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-slate-500">{heading}</p>
                      <div className="space-y-1.5">
                        {shown.filter((s) => (s.stage ?? "intake") === stage).map((s, i) => (
                          <motion.button
                            key={s.case_id}
                            initial={{ opacity: 0, y: 6 }}
                            animate={{ opacity: 1, y: 0 }}
                            transition={{ delay: i * 0.03 }}
                            disabled={sample.isPending}
                            onClick={() => sample.mutate(s.case_id)}
                            className="group flex w-full items-center gap-3 rounded-xl border border-white/[0.06] bg-white/[0.02] px-3 py-2.5 text-left transition hover:border-teal-400/30 hover:bg-teal-400/[0.04]"
                          >
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm text-slate-100">{s.title}</p>
                              <p className="text-xs text-slate-500">{s.patient} · {s.files.length} documents</p>
                            </div>
                            {sample.isPending && sample.variables === s.case_id ? (
                              <span className="size-4 animate-spin rounded-full border-2 border-teal-300 border-t-transparent" />
                            ) : (
                              <span className={cn("rounded-md px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide", stage === "after_submission"
                                ? (s.scenario === "paid_clean" ? "text-emerald-300 bg-emerald-400/10" : "text-orange-300 bg-orange-400/10")
                                : SCENARIO_TONE[s.scenario] ?? "text-amber-300 bg-amber-400/10")}>
                                {stage === "after_submission" ? (s.scenario === "paid_clean" ? "paid" : "denial")
                                  : s.scenario === "clean" ? "clean" : s.scenario === "note_mismatch" ? "AI catch" : s.scenario === "prompt_injection" ? "attack" : "issue"}
                              </span>
                            )}
                          </motion.button>
                        ))}
                      </div>
                    </div>
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
