"use client";

// Gross bookings, fare composition, surge penetration, corporate mix, and
// within-window rider repeat share.
//
// IMPORTANT framing: "gross bookings" is rider-paid volume, NOT Spinr
// revenue. Drivers keep 100% of the fare on consumer rides (CLAUDE.md: "Not
// a commission-taking marketplace"), so every label here says bookings, and
// nothing on this tab may be presented as company earnings.

import { useEffect, useState, useCallback } from "react";
import { useTheme } from "next-themes";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
} from "recharts";
import { DollarSign, Receipt, Zap, Building2, Repeat, Clock } from "lucide-react";
import { getFinancialMetrics } from "@/lib/api";
import { chartColors } from "./chart-palette";
import { StatTile, fmtMoney } from "./kpi-tile";

export interface FinancialPanelProps {
    dateRange: string;
    serviceAreaId?: string;
    refreshToken?: number;
}

export function FinancialPanel({ dateRange, serviceAreaId, refreshToken = 0 }: FinancialPanelProps) {
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");

    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const fetchData = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            setData(await getFinancialMetrics(dateRange, serviceAreaId));
        } catch (e: any) {
            setData(null);
            setError(e?.message || "Could not load financial metrics.");
        } finally {
            setLoading(false);
        }
    }, [dateRange, serviceAreaId, refreshToken]);

    useEffect(() => { void fetchData(); }, [fetchData]);

    if (loading) return <div className="py-16 text-center text-sm text-muted-foreground">Loading financial metrics…</div>;
    if (error) {
        return (
            <div className="py-16 text-center space-y-3">
                <p className="text-sm text-destructive">{error}</p>
                <button onClick={fetchData} className="text-xs font-semibold border rounded-lg px-3 py-1.5 hover:bg-muted transition-colors">Retry</button>
            </div>
        );
    }

    const surge = data?.surge || {};
    const mix = data?.mix || {};
    const riders = data?.riders || {};
    const perHour = data?.bookings_per_online_hour;

    return (
        <div className="space-y-6">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                <StatTile label="Gross bookings" value={fmtMoney(data?.gross_bookings)} Icon={DollarSign}
                          hint="rider-paid volume, not company revenue" />
                <StatTile label="Average fare" value={fmtMoney(data?.avg_fare)} Icon={Receipt}
                          hint={`${(data?.completed_rides ?? 0).toLocaleString()} completed rides`} />
                <StatTile
                    label="Bookings / online hour"
                    // null means the supply lookup failed — showing $0.00 would
                    // read as "drivers earned nothing", which is a different claim.
                    value={perHour == null ? "—" : fmtMoney(perHour)}
                    Icon={Clock}
                    hint={perHour == null ? "supply data unavailable" : `over ${data?.online_hours ?? 0} online hours`}
                />
                <StatTile label="Repeat riders" value={`${riders.repeat_rate_pct ?? 0}%`} Icon={Repeat}
                          hint={`${(riders.repeat ?? 0).toLocaleString()} of ${(riders.unique ?? 0).toLocaleString()} riders, within window`} />
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                {/* Fare composition — one measure (dollars), so one chart. */}
                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Where the money sits</CardTitle>
                        <p className="text-xs text-muted-foreground">
                            Every line item is disclosed on the rider receipt — Spinr adds no service fee.
                        </p>
                    </CardHeader>
                    <CardContent>
                        <ResponsiveContainer width="100%" height={220}>
                            <BarChart
                                layout="vertical"
                                margin={{ left: 8, right: 64 }}
                                data={[
                                    { name: "Gross bookings", value: data?.gross_bookings ?? 0 },
                                    { name: "Tips", value: data?.tips ?? 0 },
                                    { name: "Tax collected", value: data?.tax ?? 0 },
                                    { name: "Discounts given", value: data?.discounts ?? 0 },
                                ]}
                            >
                                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} horizontal={false} />
                                <XAxis type="number" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                <YAxis type="category" dataKey="name" width={120}
                                       tick={{ fontSize: 12, fill: c.axis }} axisLine={false} tickLine={false} />
                                <Tooltip contentStyle={c.tooltip} cursor={{ fill: "hsl(var(--muted))" }}
                                         formatter={(v: any) => [fmtMoney(Number(v)), "Amount"]} />
                                <Bar dataKey="value" fill={c.neutral} radius={[0, 4, 4, 0]} name="Amount"
                                     label={{ position: "right", fontSize: 11, fill: c.axis,
                                              formatter: (v: any) => fmtMoney(Number(v)) }} />
                            </BarChart>
                        </ResponsiveContainer>
                    </CardContent>
                </Card>

                <Card>
                    <CardHeader>
                        <CardTitle className="text-base">Corporate vs consumer</CardTitle>
                        <p className="text-xs text-muted-foreground">
                            Corporate accounts are the monetised side of the product; consumer rides carry
                            no commission.
                        </p>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-3">
                            <div className="rounded-lg border border-border p-3">
                                <div className="text-xs text-muted-foreground flex items-center gap-1">
                                    <Building2 className="h-3 w-3" /> Corporate
                                </div>
                                <div className="text-xl font-bold tabular-nums">{fmtMoney(mix.corporate_bookings)}</div>
                                <p className="text-xs text-muted-foreground">{(mix.corporate_rides ?? 0).toLocaleString()} rides</p>
                            </div>
                            <div className="rounded-lg border border-border p-3">
                                <div className="text-xs text-muted-foreground">Consumer</div>
                                <div className="text-xl font-bold tabular-nums">{fmtMoney(mix.consumer_bookings)}</div>
                                <p className="text-xs text-muted-foreground">{(mix.consumer_rides ?? 0).toLocaleString()} rides</p>
                            </div>
                        </div>
                        <div className="rounded-lg border border-border p-3">
                            <div className="text-xs text-muted-foreground flex items-center gap-1">
                                {/* eslint-disable-next-line no-restricted-syntax -- decorative icon tint, not a status signal (#2816) */}
                                <Zap className="h-3 w-3 text-amber-500" /> Surge penetration
                            </div>
                            <div className="text-xl font-bold tabular-nums">{surge.pct_of_rides ?? 0}%</div>
                            <p className="text-xs text-muted-foreground">
                                {(surge.rides ?? 0).toLocaleString()} rides at avg {surge.avg_multiplier ?? 1}× —{" "}
                                {fmtMoney(surge.attributable_bookings)} attributable. Auto-surge is capped at 2.5×.
                            </p>
                        </div>
                    </CardContent>
                </Card>
            </div>

            <Card>
                <CardHeader>
                    <CardTitle className="text-base">
                        Daily gross bookings
                        <span className="ml-2 text-xs font-normal text-muted-foreground">
                            ({(data?.timezone || "America/Regina").split("/").pop()} days)
                        </span>
                    </CardTitle>
                </CardHeader>
                <CardContent>
                    {!data?.daily?.length ? (
                        <p className="py-10 text-center text-sm text-muted-foreground">No completed rides in this period.</p>
                    ) : (
                        <ResponsiveContainer width="100%" height={260}>
                            <BarChart data={data.daily}>
                                <CartesianGrid strokeDasharray="3 3" stroke={c.grid} vertical={false} />
                                <XAxis dataKey="date" tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                <YAxis tick={{ fontSize: 11, fill: c.axis }} axisLine={false} tickLine={false} />
                                <Tooltip contentStyle={c.tooltip} cursor={{ fill: "hsl(var(--muted))" }}
                                         formatter={(v: any) => [fmtMoney(Number(v)), "Gross bookings"]} />
                                <Bar dataKey="gross_bookings" fill={c.neutral} radius={[4, 4, 0, 0]} name="Gross bookings" />
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

export default FinancialPanel;
