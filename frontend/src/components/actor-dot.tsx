import { Activity, Bot, CheckCircle2 } from "lucide-react";
import { cn } from "@/lib/utils";

export function ActorDot({ actor }: { actor: string }) {
  const isAgent = actor.includes("Agent");
  const isPerson = actor.includes("@");
  const Icon = isAgent ? Bot : isPerson ? CheckCircle2 : Activity;
  return (
    <div className={cn("mt-0.5 grid size-7 shrink-0 place-items-center rounded-lg ring-1",
      isAgent ? "bg-cyan-400/10 text-cyan-300 ring-cyan-400/20" : isPerson ? "bg-teal-400/10 text-teal-300 ring-teal-400/20" : "bg-violet-400/10 text-violet-300 ring-violet-400/20")}>
      <Icon className="size-3.5" />
    </div>
  );
}
