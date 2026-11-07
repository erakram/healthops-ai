import { cn } from "@/lib/utils";

/** Slow-moving colour blobs behind the page. Pure CSS, so it costs nothing to render. */
export function Aurora({ className, intensity = 1 }: { className?: string; intensity?: number }) {
  return (
    <div aria-hidden className={cn("pointer-events-none absolute inset-0 overflow-hidden", className)} style={{ opacity: intensity }}>
      <div className="absolute -left-[10%] -top-[20%] h-[60vh] w-[60vh] animate-aurora rounded-full bg-teal-500/25 blur-[120px]" />
      <div className="absolute right-[-10%] top-[10%] h-[55vh] w-[55vh] animate-aurora rounded-full bg-violet-600/20 blur-[130px] [animation-delay:-6s]" />
      <div className="absolute bottom-[-25%] left-[30%] h-[50vh] w-[50vh] animate-aurora rounded-full bg-cyan-500/20 blur-[120px] [animation-delay:-11s]" />
      <div className="grid-bg absolute inset-0" />
    </div>
  );
}
