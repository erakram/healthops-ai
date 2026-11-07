import { cn } from "@/lib/utils";

export function Logo({ compact = false, className }: { compact?: boolean; className?: string }) {
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="relative grid size-9 place-items-center rounded-xl bg-gradient-to-br from-teal-400 via-cyan-400 to-violet-500 shadow-[0_0_30px_-4px_rgba(45,212,191,0.7)]">
        <svg viewBox="0 0 24 24" className="size-5 text-ink-950" fill="none" stroke="currentColor" strokeWidth={2.6} strokeLinecap="round" strokeLinejoin="round">
          <path d="M3 12h4l2.5-6 4 12L16 12h5" />
        </svg>
      </div>
      {!compact && (
        <div className="leading-tight">
          <p className="text-[15px] font-semibold tracking-tight text-white">HealthOps <span className="text-gradient">AI</span></p>
          <p className="text-[10px] uppercase tracking-[0.18em] text-slate-500">Claims intelligence</p>
        </div>
      )}
    </div>
  );
}
