"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertOctagon, ArrowRight, Bot, CalendarClock, Check, FileText, HeartPulse, Languages, Phone, Printer, RotateCcw, Search,
  Siren, Stethoscope, UserPlus, Users,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import { toast } from "sonner";
import { Button, PageHeader, Panel, Skeleton } from "@/components/ui";
import { api, type DoctorOption, type DuplicateMatch, type OpdPatient, type OpdVisit } from "@/lib/api";
import { cn, inr, shortDate } from "@/lib/utils";

const field = "w-full rounded-xl border border-white/10 bg-white/[0.03] px-3 py-2 text-sm text-white outline-none placeholder:text-slate-500 focus:border-teal-400/50";
const SEVERITY_HI: Record<string, string> = { mild: "हल्का", moderate: "मध्यम", severe: "गंभीर", unknown: "पता नहीं" };
const SOURCE: Record<string, string> = { agent: "Intake Agent", keywords: "Keyword rules", rule: "Desk rule", default: "Default", demo: "Demo" };

const isoDay = (offset: number) => {
  const d = new Date(Date.now() + offset * 86_400_000);
  return new Date(d.getTime() - d.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
};

export default function FrontDeskPage() {
  const [patient, setPatient] = useState<OpdPatient | null>(null);
  const [visit, setVisit] = useState<OpdVisit | null>(null);
  const reset = () => { setPatient(null); setVisit(null); };
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const readOnly = !!user && user.role === "biller";
  return (
    <>
      <PageHeader eyebrow="Front office · OPD" title="Front desk"
        description="Find the patient by mobile number, type the complaint the way they say it (English, Hinglish or Hindi), and issue a token. Results are shown in English and Hindi. The Intake Agent structures the complaint and suggests a department; red flags, the doctor, the fee and the token are decided by rules you can read."
        actions={(patient || visit) && <Button variant="secondary" onClick={reset}><RotateCcw className="size-4" /> Next patient</Button>} />
      {readOnly && <p className="mb-4 rounded-xl border border-amber-400/30 bg-amber-400/[0.06] p-3 text-sm text-amber-100">
        You&apos;re signed in as a biller. Registering patients and issuing tokens need the front desk (intake) role; you can look things up.</p>}
      <Steps step={visit?.token ? 3 : visit ? 2 : patient ? 1 : 0} />
      <div className="grid gap-5 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.3fr)]">
        <div className="space-y-5">
          {patient ? <PatientCard patient={patient} onSwitch={(p) => { setPatient(p); setVisit(null); }} locked={!!visit} />
            : <FindPatient onPick={setPatient} />}
        </div>
        <div className="space-y-5">
          {!patient ? <Panel className="grid place-items-center py-20 text-center"><Users className="mb-3 size-6 text-teal-300" />
            <p className="text-slate-200">Find or register the patient first</p><p className="text-sm text-slate-500">Search by mobile number: one number often covers the whole family.</p></Panel>
            : !visit ? <Complaint patient={patient} onDone={setVisit} />
            : <Triage visit={visit} onChange={setVisit} onNext={reset} />}
        </div>
      </div>
    </>
  );
}

function Steps({ step }: { step: number }) {
  const steps = ["Patient", "Complaint", "Doctor & token", "Token issued"];
  return (
    <div className="mb-6 flex flex-wrap items-center gap-2">
      {steps.map((s, i) => (
        <div key={s} className="flex items-center gap-2">
          <motion.span animate={{ scale: i === step ? 1.05 : 1 }}
            className={cn("flex items-center gap-2 rounded-full px-3 py-1 text-xs ring-1",
              i < step ? "bg-teal-400/10 text-teal-200 ring-teal-400/25" : i === step ? "bg-white/[0.08] text-white ring-white/20" : "text-slate-500 ring-white/[0.06]")}>
            <span className={cn("grid size-4 place-items-center rounded-full text-[10px]", i < step ? "bg-teal-400 text-ink-950" : "bg-white/10")}>
              {i < step ? <Check className="size-3" /> : i + 1}</span>{s}
          </motion.span>
          {i < steps.length - 1 && <ArrowRight className="size-3 text-slate-600" />}
        </div>
      ))}
    </div>
  );
}

/* ---------- Step 1: find or register ---------- */

function FindPatient({ onPick }: { onPick: (p: OpdPatient) => void }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [registering, setRegistering] = useState(false);
  useEffect(() => { const t = setTimeout(() => setTerm(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const { data: hits, isFetching } = useQuery({ queryKey: ["opd", "search", term], queryFn: () => api.searchPatients(term), enabled: term.length >= 3 });
  return (
    <Panel className="p-5">
      <div className="mb-3 flex items-center justify-between">
        <p className="font-medium text-white">Find the patient</p>
        <button onClick={() => setRegistering(!registering)} className="flex items-center gap-1.5 text-xs text-teal-300 hover:text-teal-200">
          <UserPlus className="size-3.5" /> {registering ? "Search instead" : "New patient"}</button>
      </div>
      {registering ? <Register initialPhone={/^\d+$/.test(q) ? q : ""} onDone={onPick} /> : (
        <>
          <div className="relative">
            <Search className="absolute left-3 top-2.5 size-4 text-slate-500" />
            <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Mobile number, name, UHID or ABHA" className={cn(field, "pl-9")} />
          </div>
          <p className="mt-2 text-[11px] text-slate-500">Try 9000010001 (a family), &quot;laxmi&quot; (spelling variant) or UHID100004.</p>
          <ul className="mt-3 space-y-2">
            {isFetching && !hits && <Skeleton className="h-14 rounded-xl" />}
            {hits?.map((p, i) => (
              <motion.li key={p.id} initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }} transition={{ delay: i * 0.04 }}>
                <button onClick={() => onPick(p)} className="flex w-full items-center gap-3 rounded-xl bg-white/[0.03] p-3 text-left ring-1 ring-white/[0.06] hover:bg-white/[0.06] hover:ring-teal-400/30">
                  <Avatar p={p} />
                  <span className="min-w-0 flex-1"><span className="block truncate text-sm text-white">{p.name}</span>
                    <span className="block text-xs text-slate-400">{p.sex} · {p.age_label} · {p.uhid}{p.city ? ` · ${p.city}` : ""}</span></span>
                  <span className="font-mono text-xs text-slate-500">{p.phone}</span>
                </button>
              </motion.li>
            ))}
            {term.length >= 3 && hits?.length === 0 && (
              <li className="rounded-xl bg-white/[0.02] p-4 text-center text-sm text-slate-400">No one found.{" "}
                <button onClick={() => setRegistering(true)} className="text-teal-300 hover:underline">Register a new patient</button></li>
            )}
          </ul>
        </>
      )}
    </Panel>
  );
}

function Avatar({ p, className }: { p: OpdPatient; className?: string }) {
  const initials = p.name.split(" ").map((w) => w[0]).slice(0, 2).join("");
  return <span className={cn("grid size-9 shrink-0 place-items-center rounded-xl text-xs font-semibold",
    p.sex === "F" ? "bg-violet-400/15 text-violet-200" : p.sex === "M" ? "bg-sky-400/15 text-sky-200" : "bg-white/10 text-slate-200", className)}>{initials}</span>;
}

function Register({ initialPhone, onDone }: { initialPhone: string; onDone: (p: OpdPatient) => void }) {
  const [form, setForm] = useState({ name: "", sex: "F", phone: initialPhone, age: "", abha: "", city: "" });
  const [matches, setMatches] = useState<DuplicateMatch[] | null>(null);
  const save = useMutation({
    mutationFn: (confirm_new: boolean) => api.registerPatient({ name: form.name, sex: form.sex, phone: form.phone, age: Number(form.age),
      abha: form.abha || undefined, city: form.city || undefined, confirm_new }),
    onSuccess: (r) => {
      if (r.registered) { toast.success(`Registered ${r.patient.name}`, { description: r.patient.uhid }); onDone(r.patient); }
      else setMatches(r.possible_duplicates);
    },
    onError: (e) => toast.error(e.message),
  });
  const set = (k: keyof typeof form) => (e: { target: { value: string } }) => { setForm({ ...form, [k]: e.target.value }); setMatches(null); };
  if (matches) return (
    <motion.div initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} className="space-y-3">
      <div className="rounded-xl border border-amber-400/30 bg-amber-400/[0.06] p-3 text-sm text-amber-100">
        This may be someone already registered. Check with the patient before making a second record.</div>
      {matches.map((m) => (
        <div key={m.patient.id} className="rounded-xl bg-white/[0.03] p-3 ring-1 ring-white/[0.08]">
          <div className="flex items-center gap-3"><Avatar p={m.patient} />
            <div className="min-w-0 flex-1"><p className="text-sm text-white">{m.patient.name} <span className="text-xs text-slate-400">· {m.patient.uhid}</span></p>
              <p className="text-xs text-slate-400">{m.patient.sex} · {m.patient.age_label} · {m.patient.phone}</p></div>
            <span className="rounded-full bg-amber-400/15 px-2 py-0.5 text-xs text-amber-200">{Math.round(m.score * 100)}% match</span></div>
          <p className="mt-2 text-xs text-slate-300">{m.why.join(" · ")}</p>
          <Button variant="primary" className="mt-3 w-full py-1.5" onClick={() => onDone(m.patient)}>Same person: use this record</Button>
        </div>
      ))}
      <Button variant="secondary" className="w-full" loading={save.isPending} onClick={() => save.mutate(true)}>Different person: register new</Button>
    </motion.div>
  );
  return (
    <div className="space-y-3">
      <input value={form.name} onChange={set("name")} placeholder="Full name" className={field} />
      <div className="grid grid-cols-[1fr_90px] gap-2">
        <input value={form.phone} onChange={set("phone")} placeholder="Mobile (10 digits)" inputMode="tel" className={field} />
        <input value={form.age} onChange={set("age")} placeholder="Age" inputMode="decimal" className={field} />
      </div>
      <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
        {[["F", "Female"], ["M", "Male"], ["O", "Other"]].map(([v, l]) => (
          <button key={v} onClick={() => setForm({ ...form, sex: v })} className={cn("relative flex-1 rounded-lg py-1.5 text-sm", form.sex === v ? "text-white" : "text-slate-400")}>
            {form.sex === v && <motion.span layoutId="sex" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
            <span className="relative">{l}</span></button>))}
      </div>
      <div className="grid grid-cols-2 gap-2">
        <input value={form.abha} onChange={set("abha")} placeholder="ABHA number (optional)" className={field} />
        <input value={form.city} onChange={set("city")} placeholder="City / area" className={field} />
      </div>
      <p className="text-[11px] text-slate-500">For a baby, enter the age in years as a decimal (9 months = 0.75).</p>
      <Button variant="primary" className="w-full" disabled={!form.name.trim() || !form.phone || form.age === ""} loading={save.isPending}
        onClick={() => save.mutate(false)}><UserPlus className="size-4" /> Register</Button>
    </div>
  );
}

function PatientCard({ patient, onSwitch, locked }: { patient: OpdPatient; onSwitch: (p: OpdPatient) => void; locked: boolean }) {
  const { data } = useQuery({ queryKey: ["opd", "patient", patient.id], queryFn: () => api.opdPatient(patient.id) });
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <Panel className="p-5">
        <div className="flex items-center gap-3">
          <Avatar p={patient} className="size-12 text-sm" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-lg font-semibold text-white">{patient.name}</p>
            <p className="text-xs text-slate-400">{patient.sex} · {patient.age_label} · {patient.uhid}</p>
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 text-xs">
          <span className="flex items-center gap-1.5 text-slate-300"><Phone className="size-3.5 text-slate-500" /> {patient.phone}</span>
          <span className="truncate text-slate-300">ABHA: {patient.abha ? patient.abha.replace(/(\d{2})(\d{4})(\d{4})(\d{4})/, "$1-$2-$3-$4") : <span className="text-slate-500">not linked</span>}</span>
        </div>
        {!!data?.family.length && (
          <div className="mt-4">
            <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">Same mobile number</p>
            <div className="flex flex-wrap gap-2">{data.family.map((f) => (
              <button key={f.id} disabled={locked} onClick={() => onSwitch(f)}
                className="rounded-lg bg-white/[0.04] px-2.5 py-1 text-xs text-slate-200 ring-1 ring-white/10 hover:ring-teal-400/40 disabled:opacity-50">
                {f.name} · {f.age_label}</button>))}</div>
          </div>
        )}
        <div className="mt-4">
          <p className="mb-1.5 text-[11px] uppercase tracking-wider text-slate-500">Past OPD visits</p>
          {!data ? <Skeleton className="h-12 rounded-lg" /> : data.visits.length === 0 ? <p className="text-xs text-slate-500">First visit.</p> : (
            <ul className="space-y-1.5">{data.visits.slice(0, 4).map((v) => (
              <li key={v.id} className="rounded-lg bg-white/[0.02] px-2.5 py-1.5 text-xs">
                <span className="text-slate-300">{shortDate(v.visit_date)}</span> · <span className="text-white">{v.doctor ?? "Emergency"}</span>
                <span className="text-slate-500"> · {v.summary}</span>{v.summary_hi && <span lang="hi" className="block text-slate-500">{v.summary_hi}</span>}</li>))}</ul>
          )}
        </div>
      </Panel>
    </motion.div>
  );
}

/* ---------- Step 2: complaint ---------- */

const EXAMPLES = ["seene mai dard ho raha", "3 din se tez bukhar aur sar dard", "ghutne mein dard, seedhi chadhne mein dikkat", "गले में दर्द और खराश", "seene mein dard aur pasina aa raha hai"];

function Complaint({ patient, onDone }: { patient: OpdPatient; onDone: (v: OpdVisit) => void }) {
  const [text, setText] = useState("");
  const [day, setDay] = useState(0);
  const run = useMutation({ mutationFn: () => api.opdIntake(patient.id, text, isoDay(day)), onSuccess: onDone, onError: (e) => toast.error(e.message) });
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }}>
      <Panel className="p-5">
        <p className="mb-1 font-medium text-white">What brings {patient.name.split(" ")[0]} in today?</p>
        <p className="mb-3 text-xs text-slate-400">Type it the way the patient says it. Hinglish and Hindi are fine.</p>
        <textarea autoFocus value={text} onChange={(e) => setText(e.target.value)} rows={4} maxLength={1000}
          placeholder="e.g. 3 din se bukhar hai, sar dard bhi" className={cn(field, "resize-none text-base")} />
        <div className="mt-2 flex flex-wrap gap-1.5">{EXAMPLES.map((e) => (
          <button key={e} onClick={() => setText(e)} className="rounded-lg bg-white/[0.03] px-2 py-1 text-[11px] text-slate-400 ring-1 ring-white/[0.06] hover:text-white">{e}</button>))}</div>
        <div className="mt-4 flex flex-wrap items-center gap-3">
          <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
            {["Today", "Tomorrow"].map((l, i) => (
              <button key={l} onClick={() => setDay(i)} className={cn("relative rounded-lg px-3 py-1.5 text-sm", day === i ? "text-white" : "text-slate-400")}>
                {day === i && <motion.span layoutId="day" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                <span className="relative">{l}</span></button>))}
          </div>
          <Button variant="primary" className="ml-auto" disabled={text.trim().length < 3} loading={run.isPending} onClick={() => run.mutate()}>
            <Bot className="size-4" /> {run.isPending ? "Reading the complaint…" : "Run intake"}</Button>
        </div>
      </Panel>
    </motion.div>
  );
}

/* ---------- Step 3: triage, doctor and token ---------- */

function Triage({ visit, onChange, onNext }: { visit: OpdVisit; onChange: (v: OpdVisit) => void; onNext: () => void }) {
  const queryClient = useQueryClient();
  const n = visit.intake;
  const flags = n.red_flags;
  const [doctorId, setDoctorId] = useState<number | null>(visit.suggestion.suggested_doctor_id);
  const [mode, setMode] = useState("upi");
  const [reason, setReason] = useState("");
  const [cleared, setCleared] = useState(false);
  const chosen = visit.suggestion.options.find((o) => o.id === doctorId);
  const overriding = doctorId !== visit.suggestion.suggested_doctor_id;
  const done = (v: OpdVisit) => { onChange(v); queryClient.invalidateQueries({ queryKey: ["opd"] }); };
  const book = useMutation({ mutationFn: () => api.bookToken(visit.id, doctorId!, chosen?.fee_due ? mode : undefined, reason || undefined),
    onSuccess: (v) => { done(v); toast.success(`Token ${v.token} issued`, { description: `${v.doctor?.name}, room ${v.doctor?.room}` }); },
    onError: (e) => toast.error(e.message) });
  const emergency = useMutation({ mutationFn: () => api.sendToEmergency(visit.id), onSuccess: (v) => { done(v); toast.success("Sent to Emergency"); },
    onError: (e) => toast.error(e.message) });

  if (visit.status === "emergency") return (
    <Panel className="grid place-items-center py-14 text-center">
      <Siren className="mb-3 size-8 text-rose-300" /><p className="text-lg font-semibold text-white">Sent to Emergency</p>
      <p lang="hi" className="text-base text-rose-100">इमरजेंसी भेजा गया</p>
      <p className="mt-2 max-w-sm text-sm text-slate-400">{flags.map((f) => f.action).join(" ")}</p>
      <p lang="hi" className="max-w-sm text-sm text-slate-400">{flags.map((f) => f.action_hi).join(" ")}</p>
      <Button variant="secondary" className="mt-5" onClick={onNext}>Next patient</Button>
    </Panel>
  );
  if (visit.token) return <TokenIssued visit={visit} onNext={onNext} />;

  return (
    <div className="space-y-5">
      <AnimatePresence>{flags.length > 0 && (
        <motion.div initial={{ opacity: 0, scale: 0.98 }} animate={{ opacity: 1, scale: 1 }}
          className="relative overflow-hidden rounded-2xl border border-rose-400/40 bg-rose-500/[0.09] p-5">
          <motion.div className="absolute inset-0 bg-rose-500/10" animate={{ opacity: [0.2, 0.6, 0.2] }} transition={{ duration: 1.6, repeat: Infinity }} />
          <div className="relative">
            <div className="flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.15em] text-rose-200"><AlertOctagon className="size-5" /> Red flag: emergency, not OPD</div>
            <p lang="hi" className="mt-0.5 text-sm text-rose-100/90">खतरे का संकेत: OPD नहीं, इमरजेंसी</p>
            <ul className="mt-3 space-y-2">{flags.map((f) => (
              <li key={f.id} className="text-sm"><span className="font-medium text-white">{f.label}</span>
                {f.label_hi && <span lang="hi" className="font-medium text-white"> · {f.label_hi}</span>}
                <span className="text-rose-100/80"> · matched &ldquo;{f.matched}&rdquo;</span>
                <p className="text-xs text-rose-100/70">{f.action}</p>{f.action_hi && <p lang="hi" className="text-xs text-rose-100/70">{f.action_hi}</p>}</li>))}</ul>
            <div className="mt-4 flex flex-wrap items-center gap-2">
              <Button variant="danger" className="border-rose-300/50 bg-rose-500/30 text-white" loading={emergency.isPending} onClick={() => emergency.mutate()}><Siren className="size-4" /> Send to Emergency · इमरजेंसी भेजें</Button>
              <button onClick={() => setCleared(!cleared)} className="text-xs text-rose-100/70 underline-offset-2 hover:underline">Casualty doctor already cleared the patient?</button>
            </div>
            {cleared && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Who cleared them, and when (recorded in the audit trail)"
              className={cn(field, "mt-3 border-rose-300/30")} />}
          </div>
        </motion.div>)}
      </AnimatePresence>

      <Panel className="p-5">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2 text-xs font-semibold uppercase tracking-[0.15em] text-teal-300/80"><Bot className="size-4" /> Intake Agent</div>
          <span className="text-[11px] text-slate-500">{n.agent.used ? `${n.agent.usage?.model ?? ""} · ${((n.agent.usage?.latency_ms ?? 0) / 1000).toFixed(1)}s` : n.agent.error ? "model unavailable: rules only" : ""}</span>
        </div>
        <p className="rounded-xl bg-white/[0.03] p-3 text-sm italic text-slate-300">&ldquo;{visit.complaint}&rdquo;</p>
        <Bilingual en={n.summary_en} hi={n.summary_hi} className="mt-3" big />
        <div className="mt-3 flex flex-wrap gap-2">
          {n.symptoms.map((s, i) => (
            <motion.span key={i} initial={{ opacity: 0, y: 4 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: i * 0.05 }} title={`"${s.quote}"`}
              className="rounded-lg bg-teal-400/10 px-2 py-1 text-xs text-teal-100 ring-1 ring-teal-400/25">{s.name}{s.name_hi && <span lang="hi" className="text-teal-200/80"> · {s.name_hi}</span>}</motion.span>))}
          {n.duration && <span className="flex items-center gap-1 rounded-lg bg-white/[0.04] px-2 py-1 text-xs text-slate-300"><CalendarClock className="size-3" />{n.duration}{n.duration_hi && <span lang="hi"> · {n.duration_hi}</span>}</span>}
          {n.severity !== "unknown" && <span className={cn("rounded-lg px-2 py-1 text-xs", n.severity === "severe" ? "bg-rose-500/15 text-rose-200" : n.severity === "moderate" ? "bg-amber-400/10 text-amber-200" : "bg-white/[0.04] text-slate-300")}>{n.severity} · <span lang="hi">{SEVERITY_HI[n.severity]}</span></span>}
          {n.language && <span className="flex items-center gap-1 rounded-lg bg-white/[0.04] px-2 py-1 text-xs text-slate-400"><Languages className="size-3" />{n.language}</span>}
        </div>
        {n.dropped_symptoms > 0 && <p className="mt-2 text-[11px] text-amber-200/80">{n.dropped_symptoms} symptom(s) dropped: their words weren&apos;t in the complaint.</p>}
        <div className="mt-4 flex flex-wrap items-center gap-2 border-t border-white/[0.06] pt-4">
          <Stethoscope className="size-4 text-slate-400" /><span className="text-sm text-slate-400">Department</span>
          <span className="font-medium text-white">{n.department_name}</span><span lang="hi" className="text-sm text-slate-300">{n.department_name_hi}</span>
          <span className="rounded-full bg-white/[0.05] px-2 py-0.5 text-[11px] text-slate-300 ring-1 ring-white/10">{SOURCE[n.department_source]}</span>
          {n.keyword_department && n.agent_department && n.keyword_department !== n.agent_department &&
            <span className="text-[11px] text-slate-500">keywords said {n.keyword_department.replace("_", " ")}</span>}
        </div>
        <p className="mt-1 text-xs text-slate-400">{n.department_reason}</p>
      </Panel>

      <Panel className="p-5">
        <p className="mb-1 font-medium text-white">Doctor</p>
        {visit.suggestion.note && <p className="mb-3 rounded-xl bg-sky-400/[0.07] p-3 text-xs text-sky-100">{visit.suggestion.note}</p>}
        {visit.suggestion.options.length === 0 ? <p className="text-sm text-slate-400">No doctor in this department is on duty that day.</p> : (
          <div className="grid gap-3 md:grid-cols-2">{visit.suggestion.options.map((o, i) => (
            <DoctorCard key={o.id} o={o} suggested={o.id === visit.suggestion.suggested_doctor_id} selected={o.id === doctorId}
              onPick={() => setDoctorId(o.id)} delay={i * 0.06} />))}</div>
        )}
        {chosen && (
          <div className="mt-4 space-y-3 border-t border-white/[0.06] pt-4">
            {overriding && !flags.length && <input value={reason} onChange={(e) => setReason(e.target.value)} placeholder="Why a different doctor? (e.g. patient's request)" className={field} />}
            <div className="flex flex-wrap items-center gap-3">
              <div>
                <p className="text-xs text-slate-400">Fee</p>
                <p className="text-xl font-semibold text-white">{chosen.fee_due ? inr(chosen.fee_due) : <span className="text-emerald-300">Free</span>}</p>
                <p className="text-[11px] text-slate-500">{chosen.fee_note}</p>
              </div>
              {!!chosen.fee_due && (
                <div className="flex gap-1 rounded-xl border border-white/[0.06] bg-white/[0.02] p-1">
                  {[["upi", "UPI"], ["cash", "Cash"], ["card", "Card"]].map(([v, l]) => (
                    <button key={v} onClick={() => setMode(v)} className={cn("relative rounded-lg px-3 py-1.5 text-sm", mode === v ? "text-white" : "text-slate-400")}>
                      {mode === v && <motion.span layoutId="pay" className="absolute inset-0 rounded-lg bg-white/[0.08] ring-1 ring-white/10" />}
                      <span className="relative">{l}</span></button>))}
                </div>)}
              <Button variant="primary" className="ml-auto" loading={book.isPending}
                disabled={(flags.length > 0 && reason.trim().length < 10) || (overriding && !flags.length && reason.trim().length < 5)}
                onClick={() => book.mutate()}>Issue token <ArrowRight className="size-4" /></Button>
            </div>
          </div>
        )}
      </Panel>
    </div>
  );
}

function DoctorCard({ o, suggested, selected, onPick, delay }: { o: DoctorOption; suggested: boolean; selected: boolean; onPick: () => void; delay: number }) {
  return (
    <motion.button initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ delay }} onClick={onPick}
      className={cn("relative rounded-2xl p-4 text-left ring-1 transition", selected ? "bg-teal-400/[0.08] ring-teal-400/50" : "bg-white/[0.02] ring-white/[0.08] hover:ring-white/20")}>
      {suggested && <span className="absolute right-3 top-3 rounded-full bg-gradient-to-r from-teal-400 to-cyan-400 px-2 py-0.5 text-[10px] font-semibold text-ink-950">Suggested</span>}
      <p className="pr-16 font-medium text-white">{o.name}</p>
      <p className="text-xs text-slate-400">{o.qualification} · Room {o.room}</p>
      <p className="mt-2 text-xs text-teal-100/90">{o.reason}</p>
      <div className="mt-3 flex items-center gap-3 text-xs text-slate-400">
        <span>{o.waiting} waiting</span><span>~{o.wait_minutes} min</span><span>{o.hours}</span>
        <span className="ml-auto text-slate-200">{o.fee_due === 0 ? "Free" : inr(o.fee_due ?? o.fee)}</span>
      </div>
    </motion.button>
  );
}

function TokenIssued({ visit, onNext }: { visit: OpdVisit; onNext: () => void }) {
  return (
    <motion.div initial={{ opacity: 0, scale: 0.96 }} animate={{ opacity: 1, scale: 1 }} transition={{ type: "spring", stiffness: 200, damping: 20 }}>
      <Panel className="overflow-hidden p-0">
        <div className="bg-gradient-to-br from-teal-400/20 via-cyan-400/10 to-violet-400/10 p-8 text-center">
          <p className="text-xs uppercase tracking-[0.3em] text-teal-200/80">Token · <span lang="hi" className="tracking-normal">टोकन</span></p>
          <motion.p initial={{ scale: 0.4, opacity: 0 }} animate={{ scale: 1, opacity: 1 }} transition={{ delay: 0.15, type: "spring", stiffness: 260, damping: 14 }}
            className="bg-gradient-to-b from-white to-teal-200 bg-clip-text text-8xl font-bold tabular-nums text-transparent">{visit.token}</motion.p>
          <p className="mt-2 text-lg text-white">{visit.doctor?.name}</p>
          <p className="text-sm text-slate-300">{visit.doctor?.department_name} · Room {visit.doctor?.room}</p>
          <p lang="hi" className="text-sm text-slate-300">{visit.doctor?.department_name_hi} · कमरा {visit.doctor?.room}</p>
        </div>
        <div className="grid gap-3 p-5 sm:grid-cols-3">
          <Info label="Patient" value={visit.patient.name} />
          <Info label="Fee" value={visit.fee ? `${inr(visit.fee)} · ${visit.payment_mode?.toUpperCase()}` : "Free follow-up · मुफ़्त फॉलो-अप"} />
          <Info label="Visit" value={visit.number} />
        </div>
        {visit.override_reason && <p className="mx-5 mb-4 rounded-lg bg-white/[0.03] p-2 text-xs text-slate-400">Recorded: {visit.override_reason}</p>}
        <div className="flex flex-wrap gap-2 border-t border-white/[0.06] p-5">
          <a href={`/api/frontdesk/visits/${visit.id}/slip.pdf`} target="_blank" className="inline-flex items-center gap-2 rounded-xl bg-white/[0.06] px-4 py-2 text-sm text-slate-100 ring-1 ring-white/10 hover:bg-white/[0.1]">
            <Printer className="size-4" /> Print OPD slip</a>
          <a href="/opd-queue" className="inline-flex items-center gap-2 rounded-xl px-4 py-2 text-sm text-slate-300 hover:text-white"><FileText className="size-4" /> Queue board</a>
          <Button variant="primary" className="ml-auto" onClick={onNext}><HeartPulse className="size-4" /> Next patient</Button>
        </div>
      </Panel>
    </motion.div>
  );
}

function Info({ label, value }: { label: string; value: string }) {
  return <div><p className="text-[11px] uppercase tracking-wider text-slate-500">{label}</p><p className="truncate text-sm text-white">{value}</p></div>;
}

/** An English line with its Hindi version under it. */
function Bilingual({ en, hi, className, big }: { en: string | null | undefined; hi: string | null | undefined; className?: string; big?: boolean }) {
  if (!en && !hi) return null;
  return (
    <div className={className}>
      {en && <p className={cn("text-white", big ? "text-base" : "text-sm")}>{en}</p>}
      {hi ? <p lang="hi" className={cn("text-slate-300", big ? "text-base" : "text-sm")}>{hi}</p>
        : <p className="text-[11px] text-slate-500">Hindi summary unavailable (model not used)</p>}
    </div>
  );
}
