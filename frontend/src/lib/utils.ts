import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const money = (v: number | null | undefined, compact = false) =>
  v == null
    ? "—"
    : new Intl.NumberFormat("en-US", {
        style: "currency",
        currency: "USD",
        notation: compact ? "compact" : "standard",
        maximumFractionDigits: compact ? 1 : 2,
      }).format(v);

export const shortDate = (iso: string | null | undefined) =>
  iso ? new Date(iso.length === 10 ? `${iso}T00:00:00` : iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric" }) : "—";

export function timeAgo(iso: string) {
  const seconds = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (seconds < 45) return "just now";
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 86400) return `${Math.round(seconds / 3600)}h ago`;
  return `${Math.round(seconds / 86400)}d ago`;
}

export function displayValue(value: unknown): string {
  if (value == null || value === "") return "—";
  if (typeof value === "number") return value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return String(value);
}
