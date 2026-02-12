import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function formatCurrency(amount: number) {
  return new Intl.NumberFormat("en-CA", {
    style: "currency",
    currency: "CAD",
  }).format(amount);
}

export function formatDate(date: string | Date | undefined | null) {
  if (!date) return "—";
  const d = new Date(date);
  return d.toLocaleDateString("en-CA", {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function statusColor(status: string) {
  const map: Record<string, string> = {
    searching: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
    driver_assigned: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
    driver_arrived: "bg-indigo-500/15 text-indigo-700 dark:text-indigo-400",
    in_progress: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400",
    completed: "bg-green-500/15 text-green-700 dark:text-green-400",
    cancelled: "bg-red-500/15 text-red-700 dark:text-red-400",
    scheduled: "bg-purple-500/15 text-purple-700 dark:text-purple-400",
    open: "bg-yellow-500/15 text-yellow-700 dark:text-yellow-400",
    in_progress_ticket: "bg-blue-500/15 text-blue-700 dark:text-blue-400",
    closed: "bg-zinc-500/15 text-zinc-700 dark:text-zinc-400",
  };
  return map[status] || "bg-zinc-500/15 text-zinc-600";
}
