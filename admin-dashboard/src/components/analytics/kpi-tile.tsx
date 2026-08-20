"use client";

// Shared stat tile + KPI-vs-target readout for the marketplace tabs.
//
// A tile, not a chart: these are single headline numbers, and the dataviz
// guidance is explicit that a lone magnitude is better read as a stat tile
// than plotted. Where the backend returns a CLAUDE.md KPI target, the tile
// also renders the target and a pass/fail verdict — an operator should not
// have to remember that match rate is supposed to clear 85%.

import { Card, CardContent } from "@/components/ui/card";
import { Check, AlertTriangle, Minus } from "lucide-react";
import type { LucideIcon } from "lucide-react";

export interface KpiReading {
    key: string;
    label: string;
    actual: number;
    target: number;
    direction: "min" | "max";
    meeting_target: boolean;
}

export function StatTile({
    label,
    value,
    hint,
    Icon,
    tone = "neutral",
}: {
    label: string;
    value: React.ReactNode;
    hint?: React.ReactNode;
    Icon?: LucideIcon;
    tone?: "neutral" | "good" | "warn" | "bad";
}) {
    const toneCls =
        tone === "good" ? "text-emerald-600 dark:text-emerald-400"
        : tone === "warn" ? "text-amber-600 dark:text-amber-400"
        : tone === "bad" ? "text-red-600 dark:text-red-400"
        : "text-foreground";
    return (
        <Card>
            <CardContent className="pt-4">
                <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    {Icon && <Icon className="h-4 w-4 shrink-0" />}
                    <span className="truncate">{label}</span>
                </div>
                <div className={`text-2xl font-bold mt-1 tabular-nums ${toneCls}`}>{value}</div>
                {hint != null && <p className="text-xs text-muted-foreground mt-0.5">{hint}</p>}
            </CardContent>
        </Card>
    );
}

/** A KPI with its CLAUDE.md target. Verdict is never colour-alone — it ships
 *  with an icon and the target spelled out in words. */
export function KpiCard({ kpi }: { kpi: KpiReading }) {
    const ok = kpi.meeting_target;
    const Icon = ok ? Check : AlertTriangle;
    return (
        <Card className={ok ? undefined : "border-amber-400 dark:border-amber-600"}>
            <CardContent className="pt-4">
                <div className="text-sm text-muted-foreground truncate">{kpi.label}</div>
                <div
                    className={`text-2xl font-bold mt-1 tabular-nums ${
                        ok ? "text-emerald-600 dark:text-emerald-400" : "text-amber-600 dark:text-amber-400"
                    }`}
                >
                    {kpi.actual}%
                </div>
                <p className="text-xs text-muted-foreground mt-0.5 flex items-center gap-1">
                    <Icon className="h-3 w-3 shrink-0" aria-hidden />
                    <span>
                        {ok ? "Meeting" : "Below"} target ({kpi.direction === "min" ? "≥" : "≤"} {kpi.target}%)
                    </span>
                </p>
            </CardContent>
        </Card>
    );
}

/** Formats a duration in seconds as the largest sensible unit. Returns an
 *  em-dash for null so a missing percentile never renders as "0s". */
export function fmtSecs(secs: number | null | undefined): string {
    if (secs == null) return "—";
    const abs = Math.abs(secs);
    const sign = secs < 0 ? "−" : "";
    if (abs < 60) return `${sign}${Math.round(abs)}s`;
    if (abs < 3600) return `${sign}${Math.floor(abs / 60)}m ${Math.round(abs % 60)}s`;
    return `${sign}${(abs / 3600).toFixed(1)}h`;
}

export function fmtMoney(v: number | null | undefined): string {
    if (v == null) return "—";
    return `$${v.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}

/** Sample-size caption. A percentile over a handful of rides is not a fleet
 *  statistic and must not be presented as one. */
export function SampleNote({ n, noun = "rides" }: { n: number; noun?: string }) {
    if (!n) return <span className="text-xs text-muted-foreground">no data in window</span>;
    return (
        <span className="text-xs text-muted-foreground inline-flex items-center gap-1">
            {n < 30 && <Minus className="h-3 w-3 text-amber-500" aria-hidden />}
            n = {n.toLocaleString()} {noun}
            {n < 30 && " — small sample"}
        </span>
    );
}
