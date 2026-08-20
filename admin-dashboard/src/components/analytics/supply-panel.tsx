"use client";

// Supply & driver utilization, from the append-only driver_insurance_periods
// ledger — the same rows the SGI / Saskatchewan Transportation Act audit
// trail is built from, so these figures cannot disagree with the regulatory
// record.

import { useEffect, useState, useCallback } from "react";
import { useTheme } from "next-themes";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    BarChart, Bar, Line, LineChart, ReferenceLine,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { Users, Clock, Gauge } from "lucide-react";
import { getSupplyUtilization } from "@/lib/api";
import { chartColors } from "./chart-palette";
import { KpiCard, StatTile, type KpiReading } from "./kpi-tile";

export interface SupplyPanelProps {
    dateRange: string;
    serviceAreaId?: string;
    refreshToken?: number;
}

export function SupplyPanel({ dateRange, serviceAreaId, refreshToken = 0 }: SupplyPanelProps) {
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getSupplyUtilization(dateRange, serviceAreaId));
        } catch (e: any) {
            setData(null);
            setError(e?.message || "Could not load supply metrics.");
        } finally {
            setLoading(false);
        }
    }, [dateRange, serviceAreaId, refreshToken]);

    useEffect(() => { void fetchData(); }, [fetchData]);

    if (loading) return <div className="py-16 text-center text-sm text-muted-foreground">Loading supply metrics…</div>;
    if (error) {
        return (
            <div className="py-16 text-center space-y-3">
                <p className="text-sm text-red-600 dark:text-red-400">{error}</p>
                <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
        );
    }

    const kpis: KpiReading[] = data?.kpis || [];
    const onlineH = data?.online_hours ?? 0;

    // Time split rendered as one stacked bar so the three phases read as
    // parts of a whole; each segment is also printed numerically below.
    const split = [
        { name: "Time split", idle: data?.idle_hours ?? 0, enRoute: data?.en_route_hours ?? 0, onTrip: data?.on_trip_hours ?? 0 },
    ];

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
                {kpis.map((k) => <KpiCard key={k.key} kpi={k} />)}
                <StatTile label="Active drivers" value={(data?.active_drivers ?? 0).toLocaleString()}
                          hint="online at least once in window" Icon={Users} />
                <StatTile label="Online hours" value={onlineH.toLocaleString()} Icon={Clock} />
                <StatTile label="Avg hours / driver" value={data?.avg_online_hours_per_driver ?? 0}
                          hint="supply depth" Icon={Gauge} />
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">Where driver time goes</CardTitle>
                    <p className="text-xs text-muted-foreground">
                        Insurance Period 1 (available), 2 (en route to pickup) and 3 (passenger aboard).
                        Utilization is Period 3 over all online time; “engaged” counts Period 2 as working too,
                        since it is committed-but-unpaid time.
                    </p>
                </CardHeader>
                <CardContent>
                    {onlineH === 0 ? (
                        <p className="py-10 text-center text-sm text-muted-foreground">No driver online time recorded in this period.</p>
                    ) : (
                        <>
                            <ResponsiveContainer width="100%" height={120}>
                                <BarChart data={split} layout="vertical" margin={{ left: 8, right: 8 }}>
                                    <XAxis type="number" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} unit="h" />
                                    <YAxis type="category" dataKey="name" hide />
                                    <Tooltip contentStyle={c.tooltip} cursor={{ fill: "hsl(var(--muted))" }}
                                             formatter={(v: any, n: any) => [`${v} h`, n]} />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar dataKey="idle" stackId="t" fill={c.neutral} name="Available (P1)" />
                                    <Bar dataKey="enRoute" stackId="t" fill={c.warn} name="En route (P2)" />
                                    <Bar dataKey="onTrip" stackId="t" fill={c.good} name="On trip (P3)" radius={[0, 4, 4, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                            <div className="mt-3 grid grid-cols-2 sm:grid-cols-4 gap-3 text-sm">
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">Available (P1)</div>
                                    <div className="font-bold tabular-nums">{data?.idle_hours ?? 0} h</div>
                                </div>
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">En route (P2)</div>
                                    <div className="font-bold tabular-nums">{data?.en_route_hours ?? 0} h</div>
                                </div>
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">On trip (P3)</div>
                                    <div className="font-bold tabular-nums">{data?.on_trip_hours ?? 0} h</div>
                                </div>
                                <div className="rounded-lg border border-border p-3">
                                    <div className="text-xs text-muted-foreground">Engaged (P2+P3)</div>
                                    <div className="font-bold tabular-nums">{data?.engaged_pct ?? 0}%</div>
                                </div>
                            </div>
                        </>
                    )}
                </CardContent>
            </Card>

            {/* Hours and driver-count are different measures, so they get
                separate charts rather than a shared or second y-axis. */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">
                            Daily supply hours
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
                                    <YAxis tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} unit="h" />
                                    <Tooltip contentStyle={c.tooltip} cursor={{ fill: "hsl(var(--muted))" }}
                                             formatter={(v: any, n: any) => [`${v} h`, n]} />
                                    <Legend wrapperStyle={{ fontSize: 12 }} />
                                    <Bar dataKey="online_hours" fill={c.neutral} name="Online" radius={[4, 4, 0, 0]} />
                                    <Bar dataKey="on_trip_hours" fill={c.good} name="On trip" radius={[4, 4, 0, 0]} />
                                </BarChart>
                            </ResponsiveContainer>
                        )}
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Daily utilization</CardTitle>
                        <p className="text-xs text-muted-foreground">
                            On-trip share of online time. Target ≥ 55%.
                        </p>
                    </CardHeader>
                    <CardContent>
                        {!data?.daily?.length ? (
                            <p className="py-10 text-center text-sm text-muted-foreground">No data for selected period.</p>
                        ) : (
                            <ResponsiveContainer width="100%" height={260}>
                                <LineChart data={data.daily}>
                                    <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                    <YAxis domain={[0, 100]} tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} unit="%" />
                                    <Tooltip contentStyle={c.tooltip}
                                             formatter={(v: any) => [`${v}%`, "Utilization"]} />
                                    {/* Target line, so "is this good?" is answerable from the chart. */}
                                    <ReferenceLine y={55} stroke={c.warn} strokeDasharray="4 4"
                                                  label={{ value: "target 55%", position: "insideTopRight", fontSize: 10, fill: c.axis }} />
                                    <Line type="monotone" dataKey="utilization_pct" stroke={c.good} strokeWidth={2}
                                          dot={{ r: 3 }} name="Utilization" />
                                </LineChart>
                            </ResponsiveContainer>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

export default SupplyPanel;
