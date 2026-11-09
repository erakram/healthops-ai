"use client";

import { CheckCircle2, CircleDashed, FileSearch } from "lucide-react";
import { Panel } from "@/components/ui";
import type { Policy } from "@/lib/api";
import { cn } from "@/lib/utils";

export function PolicyCard({ policy, quote, criteriaMet }: { policy: Policy; quote: string | null; criteriaMet?: { id: string; met: boolean }[] }) {
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

