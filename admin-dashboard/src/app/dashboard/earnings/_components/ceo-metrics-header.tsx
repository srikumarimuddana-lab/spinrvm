"use client";

import {
    Car, CreditCard, Users, DollarSign, Wallet, Percent, Receipt, UserCheck, XCircle,
    Ticket, Zap, Landmark, Undo2, Filter, AlertTriangle, Search, Radar, Flag,
} from "lucide-react";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    LineChart, Line, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useTheme } from "next-themes";
import { chartColors } from "@/components/analytics/chart-palette";
import type { EarningsOverview, EarningsPeriod } from "@/lib/api";
import { PERIOD_OPTIONS, fmtMoney, fmtCount, fmtPct } from "./earnings-format";
import { MetricCard } from "./metric-card";

function CancellationMixBar({ rider, driver, system }: { rider: number; driver: number; system: number }) {
    const total = rider + driver + system;
    if (total === 0) {
        return <p className="text-xs text-muted-foreground">No cancellations in this window.</p>;
    }
    const pct = (n: number) => `${(n / total) * 100}%`;
    return (
        <div className="flex h-3 w-full rounded-md overflow-hidden bg-muted">
            <div className="bg-warning" style={{ width: pct(rider) }} title={`${rider} rider-cancelled`} />
            <div className="bg-destructive" style={{ width: pct(driver) }} title={`${driver} driver-cancelled`} />
            <div className="bg-muted-foreground/60" style={{ width: pct(system) }} title={`${system} system / no-driver-found`} />
        </div>
    );
}

export function CeoMetricsHeader({
    overview,
    loading,
    period,
    onPeriodChange,
    serviceAreaId,
    onServiceAreaChange,
    serviceAreas,
    onChartDayClick,
}: {
    overview: EarningsOverview | null;
    loading: boolean;
    period: EarningsPeriod;
    onPeriodChange: (p: EarningsPeriod) => void;
    serviceAreaId: string;
    onServiceAreaChange: (id: string) => void;
    serviceAreas: Array<{ id: string; name?: string }>;
    /** Click handler for the daily GBV chart. recharts onClick passes
     *  the activeLabel (the date key from daily_series) so the parent
     *  can drill the transaction table down to that single day. */
    onChartDayClick?: (day: string) => void;
}) {
    const m = overview?.metrics;
    const cx = overview?.cancellation_breakdown;
    const fn = overview?.ride_funnel;
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");
    return (
        <div className="space-y-4">
            <div className="flex items-end justify-between gap-3 flex-wrap">
                <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                        Business overview
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {overview?.period?.label ?? "Loading…"} · all metrics compared to the prior {overview?.period?.days ?? "—"} days
                    </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    {/* Service-area filter — scopes every metric + the
                        daily-GBV chart to a single area. "All areas" is the
                        default since CEO-level "is the business healthy"
                        questions don't usually want to pre-filter. */}
                    <div className="flex items-center gap-1.5">
                        <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                        <Select value={serviceAreaId} onValueChange={onServiceAreaChange}>
                            <SelectTrigger className="h-8 text-xs w-[180px]" aria-label="All service areas">
                                <SelectValue placeholder="All service areas" />
                            </SelectTrigger>
                            <SelectContent>
                                <SelectItem value="all" className="text-xs">All service areas</SelectItem>
                                {serviceAreas.map((a) => (
                                    <SelectItem key={a.id} value={a.id} className="text-xs">
                                        {a.name || a.id.slice(0, 8)}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>
                    <div className="inline-flex rounded-lg border border-border bg-card p-0.5">
                        {PERIOD_OPTIONS.map((opt) => (
                            <button
                                key={opt.value}
                                type="button"
                                onClick={() => onPeriodChange(opt.value)}
                                className={`px-3 py-1 text-xs font-semibold rounded transition-colors ${
                                    period === opt.value
                                        ? "bg-primary text-primary-foreground"
                                        : "text-muted-foreground hover:text-foreground"
                                }`}
                            >
                                {opt.label}
                            </button>
                        ))}
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                <MetricCard icon={DollarSign} label="GBV"               metric={m?.gbv}             format={fmtMoney} loading={loading} />
                <MetricCard icon={Wallet}     label="Net Revenue"       metric={m?.net_revenue}     format={fmtMoney} accent="text-success" loading={loading} />
                <MetricCard icon={Percent}    label="Take Rate"         metric={m?.take_rate_pct}   format={fmtPct}   loading={loading} />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816) */}
                <MetricCard icon={CreditCard} label="Spinr Pass MRR"    metric={m?.spinr_pass_mrr}  format={fmtMoney} accent="text-violet-600 dark:text-violet-400" loading={loading} />
                <MetricCard icon={Car}        label="Completed Trips"   metric={m?.completed_trips} format={fmtCount} loading={loading} />
                <MetricCard icon={Receipt}    label="Avg Fare"          metric={m?.avg_fare}        format={fmtMoney} loading={loading} />
                <MetricCard icon={UserCheck}  label="Active Drivers"    metric={m?.active_drivers}  format={fmtCount} loading={loading} />
                <MetricCard icon={Users}      label="Active Riders"     metric={m?.active_riders}   format={fmtCount} loading={loading} />
            </div>

            {/* Daily GBV trend line — single chart so the header tells a
                story instead of just being a wall of numbers. */}
            <Card className="border-border/50">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Daily GBV trend</CardTitle>
                </CardHeader>
                <CardContent>
                    {loading || !overview ? (
                        <div className="h-56 w-full bg-muted/30 rounded animate-pulse" />
                    ) : (
                        <ResponsiveContainer width="100%" height={224}>
                            <LineChart
                                data={overview.daily_series}
                                margin={{ top: 10, right: 16, left: -8, bottom: 0 }}
                                onClick={(state: any) => {
                                    // recharts passes the whole click state; activeLabel
                                    // is the date string from our daily_series rows.
                                    if (onChartDayClick && state?.activeLabel) onChartDayClick(state.activeLabel);
                                }}
                                style={{ cursor: onChartDayClick ? "pointer" : undefined }}
                            >
                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                <XAxis
                                    dataKey="date"
                                    tickFormatter={(d) => {
                                        try {
                                            return new Date(d).toLocaleDateString(undefined, { month: "short", day: "numeric" });
                                        } catch { return d; }
                                    }}
                                    tick={{ fontSize: 11 }}
                                    stroke="hsl(var(--muted-foreground))"
                                />
                                <YAxis
                                    tick={{ fontSize: 11 }}
                                    stroke="hsl(var(--muted-foreground))"
                                    tickFormatter={(v) => `$${Math.round(v).toLocaleString()}`}
                                />
                                <Tooltip
                                    contentStyle={c.tooltip}
                                    formatter={(value, name) => {
                                        const n = Number(value ?? 0);
                                        if (name === "trips") return [fmtCount(n), "Trips"] as [string, string];
                                        return [fmtMoney(n), name === "gbv" ? "GBV" : "Net Revenue"] as [string, string];
                                    }}
                                    labelFormatter={(d) => {
                                        try {
                                            return new Date(d as string).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
                                        } catch { return d as string; }
                                    }}
                                />
                                <Line type="monotone" dataKey="gbv" stroke="hsl(var(--primary))" strokeWidth={2} dot={false} />
                                <Line type="monotone" dataKey="net_revenue" stroke="hsl(var(--chart-2, 215 80% 60%))" strokeWidth={2} dot={false} strokeDasharray="4 4" />
                            </LineChart>
                        </ResponsiveContainer>
                    )}
                </CardContent>
            </Card>

            {/* Pass 2 — operational health.
                Sits below the CEO header. Cancellation rate / refunds /
                promo / surge / GST + PST collected each carry their own
                PoP delta so the operator can answer "what's leaking?"
                in the same screen. */}
            <div>
                <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground mb-3">
                    Operational health
                </h2>
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    <MetricCard icon={XCircle}  label="Cancellation Rate"   metric={m?.cancellation_rate_pct} format={fmtPct}   loading={loading} />
                    <MetricCard icon={Undo2}    label="Refunds"             metric={m?.refund_amount}        format={fmtMoney} accent="text-destructive" loading={loading} />
                    <MetricCard icon={Ticket}   label="Promo Spend"         metric={m?.promo_spend}          format={fmtMoney} accent="text-destructive" loading={loading} />
                    <MetricCard icon={Zap}      label="Surge Revenue"       metric={m?.surge_revenue}        format={fmtMoney} accent="text-warning" loading={loading} />
                    <MetricCard icon={Landmark} label="GST Collected"       metric={m?.gst_collected}        format={fmtMoney} loading={loading} />
                    <MetricCard icon={Landmark} label="PST Collected"       metric={m?.pst_collected}        format={fmtMoney} loading={loading} />
                    <MetricCard icon={CreditCard} label="Cancellation Fees" metric={m?.cancellation_revenue} format={fmtMoney} loading={loading} />
                    <MetricCard icon={XCircle}  label="Cancelled Trips"     metric={m?.cancelled_trips}      format={fmtCount} loading={loading} />
                </div>

                {/* Ride funnel — semantics documented on the ride_funnel type
                    in lib/api.ts and in migration 227. Eight cards: the four
                    progression steps, then the four cancellation outcomes
                    (rider / driver / system / after-start), which together
                    with Travelled account for every requested ride. */}
                <div className="mt-6">
                    <div className="flex items-center gap-2 mb-3">
                        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                            Ride funnel
                        </h3>
                        <span className="text-[11px] text-muted-foreground">
                            of rides requested this window · scheduled rides count on booking, and progress once dispatched
                        </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                        <MetricCard icon={Search}        label="Price Searches"        metric={fn?.price_searches}        format={fmtCount} loading={loading} />
                        <MetricCard icon={Car}           label="Rides Requested"       metric={fn?.requested}             format={fmtCount} loading={loading} />
                        <MetricCard icon={Radar}         label="Searched for Driver"   metric={fn?.reached_searching}     format={fmtCount} loading={loading} />
                        <MetricCard icon={Flag}          label="Travelled"             metric={fn?.completed}             format={fmtCount} accent="text-success" loading={loading} />
                        <MetricCard icon={XCircle}       label="Rider Cancelled"       metric={fn?.rider_cancelled}       format={fmtCount} accent="text-warning" loading={loading} />
                        <MetricCard icon={XCircle}       label="Driver Cancelled"      metric={fn?.driver_cancelled}      format={fmtCount} accent="text-destructive" loading={loading} />
                        <MetricCard icon={XCircle}       label="System / No Driver"    metric={fn?.system_cancelled}      format={fmtCount} loading={loading} />
                        <MetricCard icon={AlertTriangle} label="Cancelled After Start" metric={fn?.cancelled_after_start} format={fmtCount} accent="text-destructive" loading={loading} />
                    </div>
                </div>

                {/* Rider / driver / system cancellation mix — bar split
                    inside a single card so the cancellation_rate KPI
                    above has context (which side is leaking). */}
                {cx && (
                    <Card className="border-border/50 mt-3">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm">Cancellation mix</CardTitle>
                        </CardHeader>
                        <CardContent>
                            <CancellationMixBar
                                rider={cx.current.rider}
                                driver={cx.current.driver}
                                system={cx.current.system}
                            />
                            <div className="flex items-center gap-4 mt-2 text-[11px] text-muted-foreground">
                                <span className="inline-flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-full bg-warning" />
                                    Rider {cx.current.rider}
                                </span>
                                <span className="inline-flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-full bg-destructive" />
                                    Driver {cx.current.driver}
                                </span>
                                <span className="inline-flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-full bg-muted-foreground/60" />
                                    System / no-driver {cx.current.system}
                                </span>
                            </div>
                        </CardContent>
                    </Card>
                )}
            </div>
        </div>
    );
}
