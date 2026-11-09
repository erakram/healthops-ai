import { cn } from "@/lib/utils";

const LINKS = {
  linkedin: "https://www.linkedin.com/in/er-mohammad-akram/",
  repo: "https://github.com/erakram/healthops-ai",
};

const link = "font-medium text-sky-400 transition-colors hover:text-sky-300 hover:underline underline-offset-4";

/** "Built by Mohammad Akram · GitHub": the name opens LinkedIn, GitHub opens this project's source. */
export function SiteFooter({ className }: { className?: string }) {
  return (
    <footer className={cn("border-t border-white/[0.06] py-6 text-center text-sm text-slate-400", className)}>
      Built by{" "}
      <a href={LINKS.linkedin} target="_blank" rel="noopener noreferrer" className={link}>Mohammad Akram</a>
      <span className="mx-2 text-slate-600">·</span>
      <a href={LINKS.repo} target="_blank" rel="noopener noreferrer" className={link}>GitHub</a>
    </footer>
  );
}
