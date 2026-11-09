"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BadgeIndianRupee, Bell, CheckCheck, Clock, DoorOpen, Siren, Ticket, Undo2, UserX, Users } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { toast } from "sonner";
import { StatCard } from "@/components/stat-card";
import { Button, PageHeader, Panel, Skeleton, stagger } from "@/components/ui";
import { api, type QueueDoctor } from "@/lib/api";
import { cn, inr, shortDate } from "@/lib/utils";

export default function OpdQueuePage() {
  const { data: summary } = useQuery({ queryKey: ["opd", "summary"], queryFn: api.opdSummary, refetchInterval: 15_000 });
  const { data } = useQuery({ queryKey: ["opd", "queue"], queryFn: api.opdQueue, refetchInterval: 15_000 });
  return (
    <>
      <PageHeader eyebrow="Front office · OPD" title="OPD queue"
        description={`Live tokens for every doctor on duty${summary ? ` on ${summary.day}, ${shortDate(summary.date)}` : ""}. Calling the next token marks the patient who was with the doctor as seen. Patients who aren't there when called are skipped and can be recalled.`}
        actions={<Link href="/front-desk"><Button variant="primary"><Ticket className="size-4" /> New token</Button></Link>} />
      <motion.div variants={stagger.container} initial="hidden" animate="show" className="mb-6 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <StatCard icon={Ticket} tone="teal" label="Tokens issued" value={summary?.tokens ?? 0} foot={`${summary?.new_patients ?? 0} new registrations today`} />
        <StatCard icon={Users} tone="amber" label="Waiting" value={summary?.waiting ?? 0} foot={`${summary?.in_consult ?? 0} with a doctor now · ${summary?.seen ?? 0} seen`} />
        <StatCard icon={Siren} tone="rose" label="Sent to Emergency" value={summary?.emergencies ?? 0} foot="red flags at the desk" />
        <StatCard icon={BadgeIndianRupee} tone="emerald" label="Fees collected" value={summary?.collected ?? 0} format={(v) => inr(v)}
          foot={`${summary?.free_follow_ups ?? 0} free follow-ups · ${summary?.doctors_on_duty ?? 0} doctors on duty`} />
      </motion.div>
      {!data ? <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">{[0, 1, 2].map((i) => <Skeleton key={i} className="h-64 rounded-2xl" />)}</div>
        : data.doctors.length === 0 ? <Panel className="py-16 text-center text-slate-300">No doctors on OPD duty today.</Panel>
        : <div className="grid gap-4 lg:grid-cols-2 2xl:grid-cols-3">{data.doctors.map((d, i) => <DoctorQueue key={d.id} d={d} index={i} />)}</div>}
    </>
  );
}

function DoctorQueue({ d, index }: { d: QueueDoctor; index: number }) {
  const queryClient = useQueryClient();
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const canAct = user?.role === "intake" || user?.role === "admin";
  const act = useMutation({
    mutationFn: ({ id, action }: { id: number; action: "call" | "done" | "skip" | "recall" | "cancel" }) => api.queueAction(id, action),
    onSuccess: (v, { action }) => {
      queryClient.invalidateQueries({ queryKey: ["opd"] });
      if (action === "call") toast.success(`Token ${v.token}: ${v.patient.name}`, { description: `please go to room ${v.doctor?.room}` });
    },
    onError: (e) => toast.error(e.message),
  });
  const waiting = d.visits.filter((v) => v.status === "waiting");
  const skipped = d.visits.filter((v) => v.status === "skipped");
  const now = d.visits.find((v) => v.status === "in_consult");
  const seen = d.visits.filter((v) => v.status === "done").length;
  return (
    <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} transition={{ delay: index * 0.05 }}>
      <Panel className={cn("flex h-full flex-col p-5", !d.on_duty && "opacity-60")}>
        <div className="flex items-start justify-between gap-3">
          <div className="min-w-0">
            <p className="truncate font-medium text-white">{d.name}</p>
            <p className="text-xs text-slate-400">{d.department_name} · <span lang="hi">{d.department_name_hi}</span> · {d.hours}</p>
          </div>
          <span className="flex items-center gap-1 rounded-lg bg-white/[0.05] px-2 py-1 text-xs text-slate-300 ring-1 ring-white/10"><DoorOpen className="size-3.5" /> {d.room}</span>
        </div>

        <div className="mt-4 flex items-center gap-4 rounded-2xl bg-gradient-to-br from-teal-400/[0.12] to-transparent p-4 ring-1 ring-teal-400/20">
          <div className="relative grid size-16 shrink-0 place-items-center">
            {now && <motion.span className="absolute inset-0 rounded-2xl bg-teal-400/20" animate={{ scale: [1, 1.15, 1], opacity: [0.6, 0, 0.6] }} transition={{ duration: 2, repeat: Infinity }} />}
            <AnimatePresence mode="popLayout">
              <motion.span key={now?.token ?? "none"} initial={{ y: 20, opacity: 0 }} animate={{ y: 0, opacity: 1 }} exit={{ y: -20, opacity: 0 }}
                className="relative text-4xl font-bold tabular-nums text-white">{now?.token ?? "–"}</motion.span>
            </AnimatePresence>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] uppercase tracking-[0.2em] text-teal-200/70">Now serving</p>
            <p className="truncate text-sm text-white">{now ? now.name : "No one with the doctor"}</p>
            {now && <p className="truncate text-xs text-slate-400">{now.summary}</p>}
            {now?.summary_hi && <p lang="hi" className="truncate text-xs text-slate-400">{now.summary_hi}</p>}
          </div>
          {canAct && now && <Button className="px-2.5 py-1.5 text-xs" loading={act.isPending && act.variables?.id === now.id}
            onClick={() => act.mutate({ id: now.id, action: "done" })}><CheckCheck className="size-3.5" /> Done</Button>}
        </div>

        <div className="mt-4 flex items-center justify-between text-xs text-slate-400">
          <span>{waiting.length} waiting · {seen} seen</span>
          <span className="flex items-center gap-1"><Clock className="size-3.5" /> ~{d.wait_minutes} min for a new token</span>
        </div>
        <ul className="mt-2 flex-1 space-y-1.5">
          <AnimatePresence initial={false}>
            {waiting.map((v, i) => (
              <motion.li key={v.id} layout initial={{ opacity: 0, x: -8 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0, x: 12, height: 0 }}
                className={cn("group flex items-center gap-3 rounded-xl px-3 py-2", i === 0 ? "bg-amber-400/[0.08] ring-1 ring-amber-400/25" : "bg-white/[0.02]")}>
                <span className="w-7 text-lg font-semibold tabular-nums text-white">{v.token}</span>
                <span className="min-w-0 flex-1"><span className="block truncate text-sm text-slate-100">{v.name} <span className="text-xs text-slate-500">· {v.sex}, {v.age_label}</span></span>
                  <span className="block truncate text-xs text-slate-500">{v.summary}</span>
                  {v.summary_hi && <span lang="hi" className="block truncate text-xs text-slate-500">{v.summary_hi}</span>}</span>
                {canAct && (
                  <span className="flex gap-1 opacity-60 transition group-hover:opacity-100">
                    <IconBtn title="Not here: skip" onClick={() => act.mutate({ id: v.id, action: "skip" })}><UserX className="size-3.5" /></IconBtn>
                    {i === 0 && <IconBtn title="Call in" onClick={() => act.mutate({ id: v.id, action: "call" })} strong><Bell className="size-3.5" /></IconBtn>}
                  </span>
                )}
              </motion.li>
            ))}
          </AnimatePresence>
          {waiting.length === 0 && <li className="rounded-xl bg-white/[0.02] px-3 py-3 text-center text-xs text-slate-500">Queue is clear</li>}
        </ul>
        {skipped.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-1.5 border-t border-white/[0.06] pt-3">
            <span className="text-[11px] text-slate-500">Skipped:</span>
            {skipped.map((v) => (
              <button key={v.id} disabled={!canAct} onClick={() => act.mutate({ id: v.id, action: "recall" })} title="Recall to the queue"
                className="flex items-center gap-1 rounded-lg bg-white/[0.04] px-2 py-0.5 text-xs text-slate-300 ring-1 ring-white/10 hover:text-white">
                <Undo2 className="size-3" /> {v.token} {v.name.split(" ")[0]}</button>))}
          </div>
        )}
        {canAct && waiting.length > 0 && (
          <Button variant="primary" className="mt-4 w-full" loading={act.isPending && act.variables?.action === "call"}
            onClick={() => act.mutate({ id: waiting[0].id, action: "call" })}><Bell className="size-4" /> Call token {waiting[0].token}</Button>
        )}
      </Panel>
    </motion.div>
  );
}

function IconBtn({ children, title, onClick, strong }: { children: React.ReactNode; title: string; onClick: () => void; strong?: boolean }) {
  return <button title={title} aria-label={title} onClick={onClick}
    className={cn("grid size-7 place-items-center rounded-lg ring-1", strong ? "bg-teal-400/15 text-teal-200 ring-teal-400/30" : "bg-white/[0.04] text-slate-300 ring-white/10 hover:text-white")}>{children}</button>;
}
