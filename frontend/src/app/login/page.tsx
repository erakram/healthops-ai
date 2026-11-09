"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { ArrowRight, BadgeCheck, FileSearch, ShieldCheck, Sparkles, Stethoscope, UserCheck, Workflow } from "lucide-react";
import { motion } from "motion/react";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";
import { toast } from "sonner";
import { Aurora } from "@/components/aurora";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui";
import { api, type Role } from "@/lib/api";
import { cn } from "@/lib/utils";
import { SiteFooter } from "@/components/site-footer";

const ROLE_INFO: Record<Role, { icon: typeof UserCheck; blurb: string; gradient: string }> = {
  intake: { icon: FileSearch, blurb: "Uploads claim packets and fixes misread fields", gradient: "from-cyan-400/20 to-cyan-400/0" },
  biller: { icon: UserCheck, blurb: "Reviews findings, approves or rejects claims", gradient: "from-teal-400/20 to-teal-400/0" },
  admin: { icon: ShieldCheck, blurb: "Everything, plus the audit trail", gradient: "from-violet-400/20 to-violet-400/0" },
};

const PIPELINE = [
  { icon: FileSearch, label: "Document Agent", detail: "Reads every page" },
  { icon: Workflow, label: "Rules engine", detail: "17 deterministic checks" },
  { icon: Sparkles, label: "Review Agent", detail: "Finds what rules miss" },
  { icon: BadgeCheck, label: "Human approval", detail: "Always the final say" },
];

function LoginInner() {
  const router = useRouter();
  const next = useSearchParams().get("next") || "/dashboard";
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { data: demo } = useQuery({ queryKey: ["demo-accounts"], queryFn: api.demoAccounts });

  const onSuccess = () => router.replace(next);
  const onError = (e: Error) => toast.error(e.message);
  const demoLogin = useMutation({ mutationFn: api.demoLogin, onSuccess, onError });
  const login = useMutation({ mutationFn: () => api.login(email, password), onSuccess, onError });

  return (
    <div className="relative grid min-h-screen overflow-hidden lg:grid-cols-[1.15fr_1fr]">
      <Aurora />

      {/* Left: the pitch */}
      <section className="relative hidden flex-col justify-between p-12 lg:flex">
        <Logo />
        <div>
          <motion.h1
            initial={{ opacity: 0, y: 24 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
            className="max-w-xl text-5xl font-semibold leading-[1.08] tracking-tight text-white"
          >
            Claims that check <span className="text-gradient">themselves</span>,<br /> people who decide.
          </motion.h1>
          <motion.p
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8, delay: 0.15, ease: [0.16, 1, 0.3, 1] }}
            className="mt-5 max-w-lg text-base text-slate-400"
          >
            AI agents read claim packets, cross-check every document, and show their evidence on the page.
            A biller makes every decision, and every action lands in a tamper-evident audit trail.
          </motion.p>

          <div className="relative mt-12 max-w-xl">
            <div className="absolute left-[22px] top-6 bottom-6 w-px bg-gradient-to-b from-teal-400/60 via-cyan-400/40 to-violet-400/60" />
            {PIPELINE.map((step, i) => (
              <motion.div
                key={step.label}
                initial={{ opacity: 0, x: -20 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ delay: 0.35 + i * 0.12, duration: 0.6, ease: [0.16, 1, 0.3, 1] }}
                className="relative mb-4 flex items-center gap-4"
              >
                <div className="relative grid size-11 place-items-center rounded-xl border border-white/10 bg-ink-900/80 backdrop-blur">
                  <span className="absolute inset-0 animate-pulse-ring rounded-xl border border-teal-400/40" style={{ animationDelay: `${i * 0.45}s` }} />
                  <step.icon className="size-5 text-teal-300" />
                </div>
                <div className="panel flex-1 px-4 py-3">
                  <p className="text-sm font-medium text-white">{step.label}</p>
                  <p className="text-xs text-slate-400">{step.detail}</p>
                </div>
              </motion.div>
            ))}
          </div>
        </div>
        <p className="text-xs text-slate-500">Synthetic data only. No real patient information is used.</p>
      </section>

      {/* Right: sign in */}
      <section className="relative flex items-center justify-center p-6">
        <motion.div
          initial={{ opacity: 0, y: 20, scale: 0.98 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
          className="glow-border panel w-full max-w-md rounded-3xl p-8"
        >
          <div className="mb-6 lg:hidden"><Logo /></div>
          <div className="mb-1 flex items-center gap-2 text-teal-300">
            <Stethoscope className="size-4" />
            <span className="text-xs font-semibold uppercase tracking-[0.2em]">Operations console</span>
          </div>
          <h2 className="text-2xl font-semibold text-white">Sign in</h2>
          <p className="mt-1 text-sm text-slate-400">Pick a demo role to explore what each team member sees.</p>

          <div className="mt-6 space-y-2.5">
            {(demo ?? []).map((account, i) => {
              const info = ROLE_INFO[account.role];
              const Icon = info.icon;
              return (
                <motion.button
                  key={account.email}
                  initial={{ opacity: 0, y: 10 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ delay: 0.2 + i * 0.07 }}
                  whileHover={{ x: 4 }}
                  onClick={() => demoLogin.mutate(account.email)}
                  disabled={demoLogin.isPending}
                  className={cn(
                    "group relative flex w-full items-center gap-4 overflow-hidden rounded-2xl border border-white/[0.08] bg-gradient-to-r p-4 text-left transition-colors hover:border-white/20",
                    info.gradient,
                  )}
                >
                  <div className="grid size-10 place-items-center rounded-xl bg-white/[0.06] ring-1 ring-white/10">
                    <Icon className="size-5 text-white" />
                  </div>
                  <div className="flex-1">
                    <p className="text-sm font-medium text-white">
                      {account.name} <span className="ml-1 text-xs font-normal capitalize text-slate-400">· {account.role}</span>
                    </p>
                    <p className="text-xs text-slate-400">{info.blurb}</p>
                  </div>
                  {demoLogin.isPending && demoLogin.variables === account.email ? (
                    <span className="size-4 animate-spin rounded-full border-2 border-white/60 border-t-transparent" />
                  ) : (
                    <ArrowRight className="size-4 text-slate-500 transition-transform group-hover:translate-x-1 group-hover:text-white" />
                  )}
                </motion.button>
              );
            })}
          </div>

          <div className="my-6 flex items-center gap-3 text-xs text-slate-500">
            <span className="h-px flex-1 bg-white/10" /> or with email <span className="h-px flex-1 bg-white/10" />
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              login.mutate();
            }}
            className="space-y-3"
          >
            <input
              type="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@hospital.org"
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-teal-400/50 focus:ring-4 focus:ring-teal-400/10"
            />
            <input
              type="password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              className="w-full rounded-xl border border-white/10 bg-white/[0.03] px-4 py-2.5 text-sm text-white outline-none transition placeholder:text-slate-500 focus:border-teal-400/50 focus:ring-4 focus:ring-teal-400/10"
            />
            <Button type="submit" variant="primary" className="w-full py-2.5" loading={login.isPending}>
              Sign in
            </Button>
          </form>
        </motion.div>
      </section>
      <SiteFooter className="absolute inset-x-0 bottom-0 border-t-0 bg-transparent py-4" />
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense>
      <LoginInner />
    </Suspense>
  );
}
