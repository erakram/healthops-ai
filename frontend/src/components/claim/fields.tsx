"use client";

import { Check, Crosshair, Pencil, X } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";
import type { CodeItem, Evidence, ExtractedField, FieldSchema, LineItem } from "@/lib/api";
import { cn, displayValue, money } from "@/lib/utils";

export function ExtractedFields({ extracted, schema, editable, onFocus, onCorrect, saving }: {
  extracted: Record<string, Record<string, ExtractedField>>;
  schema: FieldSchema;
  editable: boolean;
  onFocus: (e: Evidence) => void;
  onCorrect: (documentType: string, field: string, value: string) => void;
  saving: boolean;
}) {
  const kinds = Object.keys(schema).filter((k) => extracted[k]);
  const [tab, setTab] = useState(kinds[0]);
  const active = kinds.includes(tab) ? tab : kinds[0];
  if (!active) return <p className="text-sm text-slate-500">Nothing extracted yet.</p>;
  const fields = extracted[active];
  const spec = schema[active];

  return (
    <div>
      <div className="mb-4 flex gap-1 overflow-x-auto">
        {kinds.map((k) => (
          <button key={k} onClick={() => setTab(k)}
            className={cn("relative shrink-0 rounded-lg px-3 py-1.5 text-xs transition-colors", k === active ? "text-white" : "text-slate-400 hover:text-slate-200")}>
            {k === active && <motion.span layoutId="field-tab" className="absolute inset-0 rounded-lg bg-white/[0.07] ring-1 ring-white/10" />}
            <span className="relative">{schema[k].label}</span>
          </button>
        ))}
      </div>

      <motion.div key={active} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="grid gap-2 sm:grid-cols-2">
        {spec.fields.map((f) => (
          <FieldRow key={f.name} label={f.label} field={fields[f.name]} editable={editable} saving={saving}
            onFocus={onFocus} onSave={(v) => onCorrect(active, f.name, v)} />
        ))}
      </motion.div>

      {fields.line_items && <LineItems items={fields.line_items.value as LineItem[]} onFocus={onFocus} />}
      {(["diagnosis_codes", "approved_cpt_codes"] as const).map((name) => fields[name] && (
        <div key={name} className="mt-4">
          <p className="mb-2 text-[11px] uppercase tracking-wider text-slate-500">{spec.lists[name]}</p>
          <div className="flex flex-wrap gap-1.5">
            {(fields[name].value as CodeItem[]).map((c) => (
              <button key={c.code} onClick={() => c.evidence && onFocus({ ...c.evidence, label: c.code })}
                className="rounded-lg bg-violet-400/10 px-2.5 py-1 font-mono text-xs text-violet-200 ring-1 ring-violet-400/20 transition hover:bg-violet-400/20">
                {c.code}
              </button>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

function FieldRow({ label, field, editable, saving, onFocus, onSave }: {
  label: string;
  field?: ExtractedField;
  editable: boolean;
  saving: boolean;
  onFocus: (e: Evidence) => void;
  onSave: (value: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const confidence = field?.confidence ?? 0;
  const tone = field?.corrected_by ? "bg-sky-400" : confidence >= 0.9 ? "bg-emerald-400" : confidence > 0 ? "bg-amber-400" : "bg-rose-400/70";
  const start = () => {
    setDraft(String(field?.raw ?? field?.value ?? ""));
    setEditing(true);
  };

  return (
    <div className="group rounded-xl border border-white/[0.06] bg-white/[0.02] p-3 transition hover:border-white/12">
      <div className="flex items-center justify-between gap-2">
        <p className="text-[11px] uppercase tracking-wider text-slate-500">{label}</p>
        <div className="flex items-center gap-1 opacity-60 transition group-hover:opacity-100">
          {field?.evidence && (
            <button onClick={() => onFocus({ ...field.evidence!, label })} title="Show on page"
              className="rounded p-1 text-teal-300 hover:bg-teal-400/10"><Crosshair className="size-3.5" /></button>
          )}
          {editable && !editing && (
            <button onClick={start} title="Correct this value" className="rounded p-1 text-slate-400 hover:bg-white/5 hover:text-white"><Pencil className="size-3.5" /></button>
          )}
        </div>
      </div>
      {editing ? (
        <form className="mt-1.5 flex gap-1" onSubmit={(e) => { e.preventDefault(); onSave(draft); setEditing(false); }}>
          <input autoFocus value={draft} onChange={(e) => setDraft(e.target.value)}
            className="min-w-0 flex-1 rounded-md border border-teal-400/40 bg-ink-950 px-2 py-1 text-sm text-white outline-none ring-4 ring-teal-400/10" />
          <button disabled={saving} className="rounded-md bg-teal-400/20 p-1.5 text-teal-200 hover:bg-teal-400/30"><Check className="size-3.5" /></button>
          <button type="button" onClick={() => setEditing(false)} className="rounded-md p-1.5 text-slate-400 hover:bg-white/5"><X className="size-3.5" /></button>
        </form>
      ) : (
        <p className={cn("mt-1 truncate text-sm", field ? "text-slate-100" : "italic text-slate-500")}>
          {field ? displayValue(field.raw ?? field.value) : "Not found"}
        </p>
      )}
      <div className="mt-2 flex items-center gap-2">
        <div className="h-1 flex-1 overflow-hidden rounded-full bg-white/[0.05]">
          <motion.div initial={{ width: 0 }} animate={{ width: `${(field?.corrected_by ? 1 : confidence) * 100}%` }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }} className={cn("h-full rounded-full", tone)} />
        </div>
        <span className="text-[10px] text-slate-500">
          {field?.corrected_by ? "corrected" : field?.evidence ? "on page" : field ? "unverified" : "missing"}
        </span>
      </div>
    </div>
  );
}

function LineItems({ items, onFocus }: { items: LineItem[]; onFocus: (e: Evidence) => void }) {
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-white/[0.06]">
      <table className="w-full text-sm">
        <thead className="bg-white/[0.03] text-left text-[11px] uppercase tracking-wider text-slate-500">
          <tr><th className="px-3 py-2">CPT</th><th className="px-3 py-2">Service</th><th className="px-3 py-2 text-center">Units</th><th className="px-3 py-2 text-right">Charge</th></tr>
        </thead>
        <tbody>
          {items.map((li, i) => (
            <tr key={i} onClick={() => li.evidence && onFocus({ ...li.evidence, label: `CPT ${li.cpt}` })}
              className={cn("border-t border-white/[0.04]", li.evidence && "cursor-pointer hover:bg-teal-400/[0.04]")}>
              <td className="px-3 py-2 font-mono text-cyan-200">{li.cpt}</td>
              <td className="px-3 py-2 text-slate-300">{li.description}</td>
              <td className="px-3 py-2 text-center text-slate-400">{li.units}</td>
              <td className="px-3 py-2 text-right tabular-nums text-slate-100">{typeof li.charge === "number" ? money(li.charge) : String(li.charge ?? "—")}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
