"use client";

import { motion, useInView, useMotionValue, useSpring, useTransform, type Variants } from "motion/react";
import { type ComponentProps, type ReactNode, useEffect, useRef, useState } from "react";
import type { ClaimStatus, Risk, Severity } from "@/lib/api";
import { cn } from "@/lib/utils";

/* ---------- Panel with a cursor-following glow ---------- */

export function Panel({ className, children, glow = true, ...props }: ComponentProps<"div"> & { glow?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  return (
    <div
      ref={ref}
      onMouseMove={glow ? (e) => {
        const r = ref.current!.getBoundingClientRect();
        setPos({ x: e.clientX - r.left, y: e.clientY - r.top });
      } : undefined}
      onMouseLeave={() => setPos(null)}
      className={cn("panel overflow-hidden", className)}
      {...props}
    >
      {glow && (
        <div
          className="pointer-events-none absolute inset-0 transition-opacity duration-300"
          style={{
            opacity: pos ? 1 : 0,
            background: pos ? `radial-gradient(420px circle at ${pos.x}px ${pos.y}px, rgb(45 212 191 / 0.07), transparent 45%)` : undefined,
          }}
        />
      )}
      <div className="relative">{children}</div>
    </div>
  );
}

/* ---------- Animated counter ---------- */

export function AnimatedNumber({ value, format = (v) => Math.round(v).toLocaleString("en-US"), className }: {
  value: number;
  format?: (v: number) => string;
  className?: string;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const mv = useMotionValue(0);
  const spring = useSpring(mv, { stiffness: 70, damping: 20 });
  const text = useTransform(spring, (v) => format(v));
  useEffect(() => {
    if (inView) mv.set(value);
  }, [inView, value, mv]);
  // The value animates on the client anyway; server and browser ICU can format the first frame differently.
  return <motion.span ref={ref} suppressHydrationWarning className={cn("tabular-nums", className)}>{text}</motion.span>;
}

/* ---------- Buttons ---------- */

const buttonStyles = {
  primary:
    "bg-gradient-to-r from-teal-400 via-cyan-400 to-violet-400 text-ink-950 font-semibold shadow-[0_8px_30px_-8px_rgba(45,212,191,0.6)] hover:shadow-[0_10px_40px_-6px_rgba(45,212,191,0.8)] bg-[length:200%_100%] hover:bg-right",
  secondary: "bg-white/[0.06] text-slate-100 border border-white/10 hover:bg-white/[0.1] hover:border-white/20",
  danger: "bg-rose-500/15 text-rose-200 border border-rose-400/30 hover:bg-rose-500/25",
  ghost: "text-slate-300 hover:text-white hover:bg-white/[0.06]",
};

export function Button({ variant = "secondary", className, children, loading, ...props }:
  ComponentProps<typeof motion.button> & { variant?: keyof typeof buttonStyles; loading?: boolean; children?: ReactNode }) {
  return (
    <motion.button
      whileHover={{ y: -1 }}
      whileTap={{ scale: 0.97 }}
      transition={{ type: "spring", stiffness: 500, damping: 30 }}
      className={cn(
        "inline-flex items-center justify-center gap-2 rounded-xl px-4 py-2 text-sm transition-all duration-300 disabled:pointer-events-none disabled:opacity-50",
        buttonStyles[variant],
        className,
      )}
      disabled={loading || props.disabled}
      {...props}
    >
      {loading && <span className="size-3.5 animate-spin rounded-full border-2 border-current border-t-transparent" />}
      {children}
    </motion.button>
  );
}

/* ---------- Status, risk and severity ---------- */

export const STATUS_META: Record<ClaimStatus, { label: string; tone: string; dot: string; live?: boolean }> = {
  received: { label: "Received", tone: "text-slate-300 bg-slate-400/10 ring-slate-400/20", dot: "bg-slate-400", live: true },
  extracting: { label: "Reading documents", tone: "text-cyan-200 bg-cyan-400/10 ring-cyan-400/25", dot: "bg-cyan-400", live: true },
  validating: { label: "Validating", tone: "text-violet-200 bg-violet-400/10 ring-violet-400/25", dot: "bg-violet-400", live: true },
  needs_review: { label: "Needs review", tone: "text-amber-200 bg-amber-400/10 ring-amber-400/25", dot: "bg-amber-400" },
  ready: { label: "Ready to approve", tone: "text-teal-200 bg-teal-400/10 ring-teal-400/25", dot: "bg-teal-400" },
  approved: { label: "Approved", tone: "text-emerald-200 bg-emerald-400/10 ring-emerald-400/25", dot: "bg-emerald-400", live: true },
  submitted: { label: "With payer", tone: "text-sky-200 bg-sky-400/10 ring-sky-400/25", dot: "bg-sky-400", live: true },
  rejected: { label: "Rejected", tone: "text-rose-200 bg-rose-400/10 ring-rose-400/25", dot: "bg-rose-400" },
  paid: { label: "Paid", tone: "text-emerald-100 bg-emerald-400/15 ring-emerald-300/30", dot: "bg-emerald-300" },
  denied: { label: "Denied", tone: "text-orange-200 bg-orange-400/10 ring-orange-400/30", dot: "bg-orange-400" },
  appealed: { label: "Appeal pending", tone: "text-violet-200 bg-violet-400/10 ring-violet-400/25", dot: "bg-violet-400", live: true },
  closed: { label: "Closed", tone: "text-slate-300 bg-slate-400/10 ring-slate-400/20", dot: "bg-slate-500" },
  failed: { label: "Failed", tone: "text-rose-200 bg-rose-500/15 ring-rose-400/30", dot: "bg-rose-500" },
};

export function StatusPill({ status, className }: { status: ClaimStatus; className?: string }) {
  const meta = STATUS_META[status];
  return (
    <span className={cn("inline-flex items-center gap-1.5 whitespace-nowrap rounded-full px-2.5 py-1 text-xs font-medium ring-1 ring-inset", meta.tone, className)}>
      <span className="relative flex size-1.5">
        {meta.live && <span className={cn("absolute inline-flex size-full animate-pulse-ring rounded-full", meta.dot)} />}
        <span className={cn("relative inline-flex size-1.5 rounded-full", meta.dot)} />
      </span>
      {meta.label}
    </span>
  );
}

const RISK_TONE: Record<Risk, string> = {
  low: "text-emerald-300 bg-emerald-400/10 ring-emerald-400/20",
  medium: "text-amber-300 bg-amber-400/10 ring-amber-400/20",
  high: "text-rose-300 bg-rose-400/10 ring-rose-400/25",
};

export function RiskBadge({ risk }: { risk: Risk | null }) {
  if (!risk) return <span className="text-xs text-slate-500">—</span>;
  return (
    <span className={cn("inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ring-1 ring-inset", RISK_TONE[risk])}>
      {risk}
    </span>
  );
}

export const SEVERITY_META: Record<Severity, { label: string; bar: string; text: string; ring: string }> = {
  critical: { label: "Critical", bar: "bg-rose-500", text: "text-rose-300", ring: "ring-rose-500/30" },
  high: { label: "High", bar: "bg-orange-400", text: "text-orange-300", ring: "ring-orange-400/30" },
  medium: { label: "Medium", bar: "bg-amber-300", text: "text-amber-200", ring: "ring-amber-300/25" },
  low: { label: "Low", bar: "bg-slate-400", text: "text-slate-300", ring: "ring-slate-400/20" },
};

/* ---------- Misc ---------- */

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton", className)} />;
}

export function PageHeader({ eyebrow, title, description, actions }: {
  eyebrow?: string;
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, ease: [0.16, 1, 0.3, 1] }}
      className="mb-8 flex flex-col gap-4 md:flex-row md:items-end md:justify-between"
    >
      <div>
        {eyebrow && <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-teal-300/80">{eyebrow}</p>}
        <h1 className="text-3xl font-semibold tracking-tight text-white md:text-4xl">{title}</h1>
        {description && <p className="mt-2 max-w-2xl text-sm text-slate-400">{description}</p>}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </motion.div>
  );
}

export const stagger: { container: Variants; item: Variants } = {
  container: { hidden: {}, show: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } } },
  item: {
    hidden: { opacity: 0, y: 16, filter: "blur(4px)" },
    show: { opacity: 1, y: 0, filter: "blur(0px)", transition: { duration: 0.55, ease: [0.16, 1, 0.3, 1] } },
  },
};
