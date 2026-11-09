"use client";

import { ChevronLeft, ChevronRight, Download, FileText, Maximize2 } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { type DocumentInfo, type Evidence, pageImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

/** Shows a page image with the evidence box drawn on top. Boxes are stored as fractions
 *  of the page, so they line up at any size. */
export function DocumentViewer({ documents, focus, onSelect }: {
  documents: DocumentInfo[];
  focus: Evidence | null;
  onSelect: (documentId: number) => void;
}) {
  const [docId, setDocId] = useState<number | null>(documents[0]?.id ?? null);
  const [page, setPage] = useState(1);
  const [loaded, setLoaded] = useState<string | null>(null);

  useEffect(() => {
    if (focus) {
      setDocId(focus.document_id);
      setPage(focus.page);
    }
  }, [focus]);
  useEffect(() => {
    if (docId == null && documents.length) setDocId(documents[0].id);
  }, [documents, docId]);

  const doc = documents.find((d) => d.id === docId);
  if (!doc) return null;
  const pages = Math.max(doc.pages, 1);
  const src = pageImageUrl(doc.id, page);
  // A focus with an empty box just opens the document (e.g. "show the determination letter").
  const box = focus && focus.document_id === doc.id && focus.page === page && focus.bbox[2] > focus.bbox[0] ? focus.bbox : null;

  return (
    <div className="flex h-full flex-col">
      <div className="flex gap-1 overflow-x-auto border-b border-white/[0.06] p-2">
        {documents.map((d) => (
          <button key={d.id} onClick={() => { setDocId(d.id); setPage(1); onSelect(d.id); }}
            className={cn("relative flex shrink-0 items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-colors",
              d.id === docId ? "text-white" : "text-slate-400 hover:text-slate-200")}>
            {d.id === docId && <motion.span layoutId="doc-tab" className="absolute inset-0 rounded-lg bg-white/[0.07] ring-1 ring-white/10" />}
            <FileText className="relative size-3.5" />
            <span className="relative">{d.kind ? d.label : d.filename}</span>
          </button>
        ))}
      </div>

      <div className="relative flex-1 overflow-auto bg-[radial-gradient(ellipse_at_top,rgba(45,212,191,0.06),transparent_60%)] p-4">
        <AnimatePresence mode="wait">
          <motion.div key={src} initial={{ opacity: 0, scale: 0.985 }} animate={{ opacity: 1, scale: 1 }} exit={{ opacity: 0 }}
            transition={{ duration: 0.25 }} className="relative mx-auto max-w-[620px]">
            {loaded !== src && <div className="skeleton absolute inset-0 rounded-lg" />}
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={src} alt={`${doc.filename} page ${page}`} onLoad={() => setLoaded(src)}
              className="w-full rounded-lg shadow-[0_30px_60px_-20px_rgba(0,0,0,0.8)] ring-1 ring-white/10" />
            <AnimatePresence>
              {box && loaded === src && (
                <motion.div
                  key={box.join(",")}
                  initial={{ opacity: 0, scale: 1.6 }}
                  animate={{ opacity: 1, scale: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ type: "spring", stiffness: 260, damping: 22 }}
                  className="pointer-events-none absolute rounded-[4px] border-2 border-teal-300 bg-teal-300/15 shadow-[0_0_0_4px_rgba(45,212,191,0.15),0_0_30px_4px_rgba(45,212,191,0.45)]"
                  style={{
                    left: `calc(${box[0] * 100}% - 4px)`,
                    top: `calc(${box[1] * 100}% - 3px)`,
                    width: `calc(${(box[2] - box[0]) * 100}% + 8px)`,
                    height: `calc(${(box[3] - box[1]) * 100}% + 6px)`,
                  }}
                >
                  <span className="absolute inset-0 animate-pulse-ring rounded-[4px] border-2 border-teal-300/70" />
                  {focus?.label && (
                    <span className="absolute -top-6 left-0 whitespace-nowrap rounded-md bg-teal-300 px-1.5 py-0.5 text-[10px] font-semibold text-ink-950">{focus.label}</span>
                  )}
                </motion.div>
              )}
            </AnimatePresence>
          </motion.div>
        </AnimatePresence>
      </div>

      <div className="flex items-center justify-between border-t border-white/[0.06] px-3 py-2 text-xs text-slate-400">
        <span className="truncate">{doc.filename}</span>
        <div className="flex items-center gap-1">
          <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded p-1 hover:bg-white/5 disabled:opacity-30"><ChevronLeft className="size-4" /></button>
          <span className="tabular-nums">{page} / {pages}</span>
          <button disabled={page >= pages} onClick={() => setPage(page + 1)} className="rounded p-1 hover:bg-white/5 disabled:opacity-30"><ChevronRight className="size-4" /></button>
          <a href={`/api/documents/${doc.id}/file`} target="_blank" className="ml-2 rounded p-1 hover:bg-white/5" title="Open PDF"><Maximize2 className="size-3.5" /></a>
          <a href={`/api/documents/${doc.id}/file`} download className="rounded p-1 hover:bg-white/5" title="Download"><Download className="size-3.5" /></a>
        </div>
      </div>
    </div>
  );
}
