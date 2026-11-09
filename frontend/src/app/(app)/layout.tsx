"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ClipboardCheck, ConciergeBell, FileStack, ListOrdered, Hourglass, Landmark, Tags, Wallet, LayoutDashboard, LogOut, Menu, Scale, ScrollText, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { toast } from "sonner";
import { Aurora } from "@/components/aurora";
import { Logo } from "@/components/logo";
import { api } from "@/lib/api";
import { useLiveEvents } from "@/lib/live";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/dashboard", label: "Command center", icon: LayoutDashboard },
  { href: "/front-desk", label: "Front desk (OPD)", icon: ConciergeBell },
  { href: "/opd-queue", label: "OPD queue", icon: ListOrdered, badge: "opd" as const },
  { href: "/authorizations", label: "Authorizations", icon: ClipboardCheck, badge: "auths" as const },
  { href: "/coding", label: "Coding", icon: Tags, badge: "coding" as const },
  { href: "/claims", label: "Claims", icon: FileStack, badge: "claims" as const },
  { href: "/denials", label: "Denials", icon: Scale, badge: "denials" as const },
  { href: "/receivables", label: "Receivables", icon: Hourglass, badge: "ar" as const },
  { href: "/billing", label: "Patient billing", icon: Wallet },
  { href: "/reconciliation", label: "Reconciliation", icon: Landmark },
  { href: "/audit", label: "Audit trail", icon: ScrollText, roles: ["biller", "admin"] },
];

export default function AppLayout({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const { data: user } = useQuery({ queryKey: ["me"], queryFn: api.me });
  const { data: waiting } = useQuery({
    queryKey: ["claims", "awaiting"],
    queryFn: () => api.claims({ status: "needs_review,ready" }),
    refetchInterval: 30_000,
  });
  const { data: denials } = useQuery({ queryKey: ["denials", "summary"], queryFn: api.denialsSummary, refetchInterval: 30_000 });
  const { data: auths } = useQuery({ queryKey: ["auths", "summary"], queryFn: api.authorizationsSummary, refetchInterval: 30_000 });
  const { data: coding } = useQuery({ queryKey: ["encounters", "summary"], queryFn: api.encountersSummary, refetchInterval: 30_000 });
  const { data: ar } = useQuery({ queryKey: ["ar", "summary"], queryFn: api.arSummary, refetchInterval: 60_000 });
  const { data: opd } = useQuery({ queryKey: ["opd", "summary"], queryFn: api.opdSummary, refetchInterval: 30_000 });
  const badges = { opd: opd?.waiting ?? 0, claims: waiting?.length ?? 0, denials: denials?.open ?? 0, auths: auths?.needs_documentation ?? 0,
    coding: coding?.review ?? 0, ar: ar?.due_for_follow_up ?? 0 };
  const connected = useLiveEvents((event) => {
    if (event.type === "auth.updated" && ["approved", "pended", "denied"].includes(event.status ?? "")) {
      const words = { approved: "approved", pended: "pended: the payer wants more information", denied: "denied" } as Record<string, string>;
      toast.message(`Prior authorization ${words[event.status!]}`, {
        action: { label: "Open", onClick: () => router.push(`/authorizations/${event.authorization_id}`) },
      });
    }
    if (event.type === "claim.updated" && event.status === "needs_review") toast.message("A claim needs review", {
      description: "The agents finished and found issues.",
      action: { label: "Open", onClick: () => router.push(`/claims/${event.claim_id}`) },
    });
  });
  const logout = useMutation({ mutationFn: api.logout, onSuccess: () => router.replace("/login") });

  const sidebar = (
    <div className="flex h-full flex-col">
      <div className="px-5 pt-6 pb-8"><Logo /></div>
      <nav className="flex-1 space-y-1 px-3">
        {NAV.filter((n) => !n.roles || (user && n.roles.includes(user.role))).map((item) => {
          const active = pathname === item.href || (item.href !== "/dashboard" && pathname.startsWith(item.href));
          return (
            <Link key={item.href} href={item.href} onClick={() => setOpen(false)}
              className={cn("relative flex items-center gap-3 rounded-xl px-3 py-2.5 text-sm transition-colors",
                active ? "text-white" : "text-slate-400 hover:text-slate-100")}>
              {active && (
                <motion.span layoutId="nav-active"
                  className="absolute inset-0 rounded-xl border border-white/10 bg-gradient-to-r from-teal-400/15 via-cyan-400/5 to-transparent"
                  transition={{ type: "spring", stiffness: 380, damping: 32 }} />
              )}
              {active && <motion.span layoutId="nav-bar" className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-gradient-to-b from-teal-300 to-violet-400" />}
              <item.icon className="relative size-[18px]" />
              <span className="relative flex-1">{item.label}</span>
              {item.badge && !!badges[item.badge] && (
                <motion.span key={badges[item.badge]} initial={{ scale: 0 }} animate={{ scale: 1 }}
                  className={cn("relative rounded-full px-2 py-0.5 text-[11px] font-semibold ring-1",
                    item.badge === "denials" ? "bg-orange-400/15 text-orange-200 ring-orange-400/30"
                      : item.badge === "auths" ? "bg-violet-400/15 text-violet-200 ring-violet-400/30"
                      : item.badge === "coding" ? "bg-cyan-400/15 text-cyan-200 ring-cyan-400/30"
                      : item.badge === "opd" ? "bg-teal-400/15 text-teal-200 ring-teal-400/30"
                      : item.badge === "ar" ? "bg-sky-400/15 text-sky-200 ring-sky-400/30" : "bg-amber-400/15 text-amber-200 ring-amber-400/30")}>
                  {badges[item.badge]}
                </motion.span>
              )}
            </Link>
          );
        })}
      </nav>

      <div className="mx-3 mb-3 rounded-xl border border-white/[0.06] bg-white/[0.02] p-3">
        <div className="flex items-center gap-2 text-xs">
          <span className="relative flex size-2">
            {connected && <span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-emerald-400" />}
            <span className={cn("relative inline-flex size-2 rounded-full", connected ? "bg-emerald-400" : "bg-slate-500")} />
          </span>
          <span className="text-slate-300">{connected ? "Agents online" : "Reconnecting…"}</span>
        </div>
        <p className="mt-1 text-[11px] text-slate-500">Live updates from the claim pipeline</p>
      </div>

      {user && (
        <div className="flex items-center gap-3 border-t border-white/[0.06] p-4">
          <div className="grid size-9 place-items-center rounded-full bg-gradient-to-br from-teal-400/30 to-violet-500/30 text-sm font-semibold text-white ring-1 ring-white/15">
            {user.name.split(" ").map((p) => p[0]).join("")}
          </div>
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm font-medium text-white">{user.name}</p>
            <p className="text-xs capitalize text-slate-400">{user.role}</p>
          </div>
          <button onClick={() => logout.mutate()} className="rounded-lg p-2 text-slate-400 transition hover:bg-white/5 hover:text-white" title="Sign out">
            <LogOut className="size-4" />
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div className="relative min-h-screen">
      <Aurora intensity={0.45} className="fixed" />
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-64 border-r border-white/[0.06] bg-ink-950/60 backdrop-blur-2xl lg:block">
        {sidebar}
      </aside>

      {/* Mobile */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-white/[0.06] bg-ink-950/80 px-4 py-3 backdrop-blur-xl lg:hidden">
        <Logo />
        <button onClick={() => setOpen(true)} className="rounded-lg p-2 text-slate-300 hover:bg-white/5"><Menu className="size-5" /></button>
      </header>
      <AnimatePresence>
        {open && (
          <>
            <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              onClick={() => setOpen(false)} className="fixed inset-0 z-40 bg-black/60 backdrop-blur-sm lg:hidden" />
            <motion.aside initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }} transition={{ type: "spring", damping: 30, stiffness: 300 }}
              className="fixed inset-y-0 left-0 z-50 w-64 border-r border-white/10 bg-ink-950 lg:hidden">
              <button onClick={() => setOpen(false)} className="absolute right-3 top-5 rounded-lg p-2 text-slate-400"><X className="size-4" /></button>
              {sidebar}
            </motion.aside>
          </>
        )}
      </AnimatePresence>

      <main className="relative lg:pl-64">
        <div className="mx-auto max-w-[1400px] px-4 py-8 md:px-8 md:py-10">{children}</div>
      </main>
    </div>
  );
}
