"use client";

import { TrendingUp, TrendingDown } from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import type { MetricWithDelta } from "@/lib/api";

export function DeltaChip({ pct }: { pct: number | null }) {
    if (pct === null) return <span className="text-[11px] text-muted-foreground">—</span>;
    if (pct === 0) return <span className="text-[11px] text-muted-foreground">±0.0%</span>;
    const up = pct > 0;
    const Icon = up ? TrendingUp : TrendingDown;
    const color = up
        ? "text-success bg-success/10"
        : "text-destructive bg-destructive/10";
    return (
        <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${color}`}>
            <Icon className="h-3 w-3" />
            {up ? "+" : ""}{pct.toFixed(1)}%
        </span>
    );
}

export function MetricCard({
    icon: Icon,
    label,
    metric,
    format,
    accent,
    loading,
}: {
    icon: any;
    label: string;
    metric: MetricWithDelta | undefined;
    format: (n: number) => string;
    accent?: string;
    loading: boolean;
}) {
    return (
        <Card className="border-border/50">
            <CardContent className="p-4">
                <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                    <Icon className="h-3.5 w-3.5" />
                    <span className="truncate">{label}</span>
                </div>
                {loading || !metric ? (
                    <div className="mt-2 h-7 w-24 bg-muted rounded animate-pulse" />
                ) : (
                    <>
                        <p className={`text-2xl font-bold tabular-nums mt-1.5 ${accent || ""}`}>{format(metric.current)}</p>
                        <div className="flex items-center gap-1.5 mt-1">
                            <DeltaChip pct={metric.delta_pct} />
                            <span className="text-[10px] text-muted-foreground">vs {format(metric.previous)}</span>
                        </div>
                    </>
                )}
            </CardContent>
        </Card>
    );
}
