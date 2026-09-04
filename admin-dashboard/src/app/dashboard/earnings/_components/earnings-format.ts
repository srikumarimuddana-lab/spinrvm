import { formatCurrency } from "@/lib/utils";
import type { EarningsPeriod } from "@/lib/api";

export const PERIOD_OPTIONS: Array<{ value: EarningsPeriod; label: string }> = [
    { value: "7d", label: "7d" },
    { value: "30d", label: "30d" },
    { value: "mtd", label: "MTD" },
    { value: "ytd", label: "YTD" },
];

// Format helpers for the CEO header. Money + count + percent each render
// differently so the row reads at a glance instead of every number looking
// like a dollar amount.
export const fmtMoney = (n: number) => formatCurrency(n);
export const fmtCount = (n: number) => n.toLocaleString();
export const fmtPct = (n: number) => `${n.toFixed(2)}%`;

// Helpers for time formatting in the payouts header. "1.5 h" reads
// better than "1 h 30 m" at small font sizes, so use decimal hours
// unless the value drops below an hour.
export function fmtHours(n: number) {
    if (n <= 0) return "—";
    if (n < 1) return `${Math.round(n * 60)} min`;
    if (n < 24) return `${n.toFixed(1)} h`;
    return `${(n / 24).toFixed(1)} d`;
}

// Format a YYYY-MM string for display.
export function fmtPeriodKey(key: string): string {
    try {
        const [y, m] = key.split("-").map(Number);
        return new Date(y, (m || 1) - 1, 1).toLocaleString(undefined, { month: "long", year: "numeric" });
    } catch {
        return key;
    }
}
