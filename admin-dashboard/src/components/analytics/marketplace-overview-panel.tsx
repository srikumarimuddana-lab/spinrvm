"use client";

// Marketplace health: the request → matched → accepted → completed funnel,
// the CLAUDE.md KPI targets that had no surface before, and where demand is
// being lost.

import { useEffect, useState, useCallback } from "react";
import { useTheme } from "next-themes";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { Activity, TrendingDown, UserX, CarFront, AlertTriangle } from "lucide-react";
import { getMarketplaceFunnel } from "@/lib/api";
import { chartColors } from "./chart-palette";
import { KpiCard, StatTile, type KpiReading } from "./kpi-tile";

export interface MarketplaceOverviewPanelProps {
    dateRange: string;
    serviceAreaId?: string;
    refreshToken?: number;
}

export function MarketplaceOverviewPanel({
    dateRange, serviceAreaId, refreshToken = 0,
}: MarketplaceOverviewPanelProps) {
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getMarketplaceFunnel(dateRange, serviceAreaId));
        } catch (e: any) {
            setData(null);
            setError(e?.message || "Could not load marketplace metrics.");
        } finally {
            setLoading(false);
        }
    }, [dateRange, serviceAreaId, refreshToken]);

    useEffect(() => { void fetchData(); }, [fetchData]);

    if (loading) return <div className="py-16 text-center text-sm text-muted-foreground">Loading marketplace metrics…</div>;

    // Error and empty are different states — a failed request must never
    // render as "0 rides requested".
    if (error) {
        return (
            <div className="py-16 text-center space-y-3">
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
                <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">
                    Retry
                </button>
            </div>
        );
    }

    const stages: any[] = data?.stages || [];
    const requested = stages[0]?.count ?? 0;
    const rates = data?.rates || {};
    const kpis: KpiReading[] = data?.kpis || [];
    const dropoff = data?.dropoff || {};
    const fallback = data?.cancels_unattributed_fallback ?? 0;
    const cancelled = data?.cancelled ?? 0;

    // Funnel rendered as a horizontal bar chart: one series, magnitude by
    // stage, with the count printed on every bar so identity never rests on
    // colour alone (the light-mode palette carries a contrast WARN).
    const funnelData = stages.map((s: any) => ({
        ...s,
        pct: requested ? Math.round((s.count / requested) * 100) : 0,
    }));

    return (
        <div className="space-y-6">
            {/* KPI targets — the reason this tab exists. */}
            {kpis.length > 0 && (
                <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
                    {kpis.map((k) => <KpiCard key={k.key} kpi={k} />)}
                </div>
            )}

            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Rides requested" value={requested.toLocaleString()} Icon={Activity} />
                <StatTile
                    label="Fulfilment rate"
                    value={`${rates.fulfilment_rate ?? 0}%`}
                    hint="requested → completed"
                    Icon={CarFront}
                />
                <StatTile
                    label="Unmet demand"
                    value={`${rates.unmet_demand_rate ?? 0}%`}
                    hint={`${(data?.no_supply ?? 0).toLocaleString()} with no driver found`}
                    Icon={UserX}
                    tone={(rates.unmet_demand_rate ?? 0) > 5 ? "warn" : "neutral"}
                />
                <StatTile
                    label="Cancelled"
                    value={cancelled.toLocaleString()}
                    hint={`${rates.cancellation_rate ?? 0}% of requests`}
                    Icon={TrendingDown}
                />
            </div>

            {/* Funnel */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Conversion funnel</CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Stages come from durable timestamps, not current status — a ride matched
                        then cancelled still counts as matched.
                    </p>
                </CardHeader>
                <CardContent>
                    {requested === 0 ? (
                        <p className="py-10 text-center text-sm text-muted-foreground">No rides requested in this period.</p>
                    ) : (
                        <>
                            <ResponsiveContainer width="100%" height={220}>
                                <BarChart data={funnelData} layout="vertical" margin={{ left: 8, right: 48 }}>
                                    <CartesianGrid strokeDasharray="3 3" stroke={c.grid} horizontal={false} />
                                    <XAxis type="number" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                    <YAxis
                                        type="category" dataKey="label" width={120}
                                        tick={{ fontSize: 12, fill: c.axis }} axisLine={false} tickLine={false}
                                    />
                                    <Tooltip
                                        contentStyle={c.tooltip}
                                        cursor={{ fill: "hsl(var(--muted))" }}
                                        formatter={(v: any, _n: any, p: any) => [`${Number(v).toLocaleString()} (${p.payload.pct}%)`, "Rides"]}
                                    />
                                    <Bar dataKey="count" fill={c.neutral} radius={[0, 4, 4, 0]} name="Rides"
                                         label={{ position: "right", fontSize: 11, fill: c.axis,
                                                  formatter: (v: any) => Number(v).toLocaleString() }} />
                                </BarChart>
                            </ResponsiveContainer>
                            {/* Table view — the relief the light-mode contrast WARN obligates,
                                and the precise numbers a chart can only approximate. */}
                            <div className="mt-4 grid grid-cols-3 gap-3 text-sm">
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">Lost before match</div>
                                    <div className="font-bold tabular-nums">{(dropoff.request_to_match ?? 0).toLocaleString()}</div>
                                </div>
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">Lost before accept</div>
                                    <div className="font-bold tabular-nums">{(dropoff.match_to_accept ?? 0).toLocaleString()}</div>
                                </div>
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">Lost after accept</div>
                                    <div className="font-bold tabular-nums">{(dropoff.accept_to_complete ?? 0).toLocaleString()}</div>
                                </div>
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>

            {/* Daily trend */}
            <Card>
                <CardHeader>
                    <CardTitle className="text-base">
                        Daily volume
                        <span className="ml-2 text-xs font-normal text-muted-foreground">
                            ({(data?.timezone || "America/Regina").split("/").pop()} days)
                        </span>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {!data?.daily?.length ? (
                        <p className="py-10 text-center text-sm text-muted-foreground">No data for selected period.</p>
                    ) : (
                        <ResponsiveContainer width="100%" height={260}>
                            <BarChart data={data.daily}>
                                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                                <XAxis dataKey="date" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                <YAxis tick={{ fontSize: 11, fill: c.axis }} allowDecimals={false} axisLine={false} tickLine={false} />
                                <Tooltip contentStyle={c.tooltip} cursor={{ fill: "hsl(var(--muted))" }} />
                                <Legend wrapperStyle={{ fontSize: 12 }} />
                                <Bar dataKey="completed" stackId="d" fill={c.good} name="Completed" />
                                <Bar dataKey="cancelled" stackId="d" fill={c.bad} name="Cancelled" />
                                <Bar dataKey="no_supply" stackId="d" fill={c.warn} name="No driver found" radius={[4, 4, 0, 0]} />
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </CardContent>
            </Card>

            {/* Attribution honesty note */}
            {fallback > 0 && (
                <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-200">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                    <span>
                        {fallback.toLocaleString()} of {cancelled.toLocaleString()} cancellations predate the
                        structured attribution columns and were classified by matching the free-text reason.
                        The rider/driver split above is that much less certain.
                    </span>
                </div>
            )}
        </div>
    );
}

export default MarketplaceOverviewPanel;
