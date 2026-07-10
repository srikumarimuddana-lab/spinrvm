"use client";

import { useEffect, useState } from "react";
import { getEarnings, getEarningsOverview, getEarningsRides, getServiceAreas, getSubscriptionStats, type EarningsOverview, type EarningsPeriod, type EarningsRide, type MetricWithDelta } from "@/lib/api";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import ReferralsPanel from "@/components/referrals-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Download, Car, CreditCard, Users, TrendingUp, TrendingDown, DollarSign, UserPlus, Clock, MapPin, X, GitCompareArrows, Wallet, CheckCircle, AlertTriangle, Percent, Receipt, UserCheck, XCircle, Ticket, Zap, Landmark, Undo2, Filter, Hourglass, Activity, Gift, Search, Radar, Flag } from "lucide-react";
import { getPayouts, getPayoutStats, getPayoutsOverview, retryPayout, bulkRetryPayouts, closePayoutPeriod, type PayoutsOverview } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useAuthStore } from "@/store/authStore";
import { Legend } from "recharts";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid,
} from "recharts";

const tooltipStyle = {
    fontSize: 12, borderRadius: 10,
    border: '1px solid hsl(var(--border))',
    background: 'hsl(var(--card))',
    boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
};

export default function EarningsPage() {
    const { allowed } = useRequireModule("earnings");
    const [tab, setTab] = useState<"rides" | "spinr-pass" | "payouts" | "referrals">("rides");

    // The Referrals tab calls /api/admin/referrals/leaderboard, which is gated by
    // the `drivers` module on the backend. Only show it to admins who actually
    // have drivers access — otherwise they'd see the tab and hit a 403.
    const user = useAuthStore((s) => s.user);
    const canSeeReferrals =
        user?.role === "super_admin" || user?.role === "admin" || (user?.modules ?? []).includes("drivers");

    if (!allowed) return null;
    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Earnings & Payouts</h1>
                    <p className="text-muted-foreground mt-1">Platform revenue, subscriptions, and driver payouts</p>
                </div>
            </div>

            {/* Tabs */}
            <div className="flex gap-1 bg-muted rounded-xl p-1 w-fit">
                <button onClick={() => setTab("rides")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "rides" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <Car className="h-4 w-4" /> Ride Earnings
                </button>
                <button onClick={() => setTab("spinr-pass")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "spinr-pass" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <CreditCard className="h-4 w-4" /> Spinr Pass
                </button>
                <button onClick={() => setTab("payouts")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "payouts" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <Wallet className="h-4 w-4" /> Payouts
                </button>
                {canSeeReferrals && (
                    <button onClick={() => setTab("referrals")}
                        className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "referrals" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                        <Gift className="h-4 w-4" /> Referrals
                    </button>
                )}
            </div>

            {tab === "rides" && <RideEarningsTab />}
            {tab === "spinr-pass" && <SpinrPassRevenueTab />}
            {tab === "payouts" && <PayoutsTab />}
            {tab === "referrals" && canSeeReferrals && (
                <div className="pt-1">
                    {/* Full referral program, inlined here (no link-out) so the
                        redemption funnel, payouts, trends, and top referrers live
                        right in Earnings. Same ReferralsPanel the standalone page
                        renders — one source, no divergence. */}
                    <ReferralsPanel />
                </div>
            )}
        </div>
    );
}

// ─── Ride Earnings Tab (existing) ───

const PERIOD_OPTIONS: Array<{ value: EarningsPeriod; label: string }> = [
    { value: "7d", label: "7d" },
    { value: "30d", label: "30d" },
    { value: "mtd", label: "MTD" },
    { value: "ytd", label: "YTD" },
];

// Format helpers for the CEO header. Money + count + percent each render
// differently so the row reads at a glance instead of every number looking
// like a dollar amount.
const fmtMoney = (n: number) => formatCurrency(n);
const fmtCount = (n: number) => n.toLocaleString();
const fmtPct = (n: number) => `${n.toFixed(2)}%`;

function DeltaChip({ pct }: { pct: number | null }) {
    if (pct === null) return <span className="text-[11px] text-muted-foreground">—</span>;
    if (pct === 0) return <span className="text-[11px] text-muted-foreground">±0.0%</span>;
    const up = pct > 0;
    const Icon = up ? TrendingUp : TrendingDown;
    const color = up
        ? "text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20"
        : "text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20";
    return (
        <span className={`inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-semibold ${color}`}>
            <Icon className="h-3 w-3" />
            {up ? "+" : ""}{pct.toFixed(1)}%
        </span>
    );
}

function MetricCard({
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

function CeoMetricsHeader({
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
    return (
        <div className="space-y-4">
            <div className="flex items-end justify-between gap-3 flex-wrap">
                <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                        Business overview
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {overview ? overview.period.label : "Loading…"} · all metrics compared to the prior {overview?.period.days ?? "—"} days
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
                            <SelectTrigger className="h-8 text-xs w-[180px]">
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
                <MetricCard icon={Wallet}     label="Net Revenue"       metric={m?.net_revenue}     format={fmtMoney} accent="text-emerald-600 dark:text-emerald-400" loading={loading} />
                <MetricCard icon={Percent}    label="Take Rate"         metric={m?.take_rate_pct}   format={fmtPct}   loading={loading} />
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
                                    contentStyle={tooltipStyle}
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
                    <MetricCard icon={Undo2}    label="Refunds"             metric={m?.refund_amount}        format={fmtMoney} accent="text-red-600 dark:text-red-400" loading={loading} />
                    <MetricCard icon={Ticket}   label="Promo Spend"         metric={m?.promo_spend}          format={fmtMoney} accent="text-red-600 dark:text-red-400" loading={loading} />
                    <MetricCard icon={Zap}      label="Surge Revenue"       metric={m?.surge_revenue}        format={fmtMoney} accent="text-amber-600 dark:text-amber-400" loading={loading} />
                    <MetricCard icon={Landmark} label="GST Collected"       metric={m?.gst_collected}        format={fmtMoney} loading={loading} />
                    <MetricCard icon={Landmark} label="PST Collected"       metric={m?.pst_collected}        format={fmtMoney} loading={loading} />
                    <MetricCard icon={CreditCard} label="Cancellation Fees" metric={m?.cancellation_revenue} format={fmtMoney} loading={loading} />
                    <MetricCard icon={XCircle}  label="Cancelled Trips"     metric={m?.cancelled_trips}      format={fmtCount} loading={loading} />
                </div>

                {/* Ride funnel — a created_at cohort of the rides requested in
                    this window, followed through to outcome. Reads top-to-bottom:
                    price searches → requested → searched for a driver →
                    travelled, then the three cancellation outcomes. `Travelled`
                    is cohort-based, so it differs from the completion-date
                    "Completed Trips" KPI above — same window, different lens. */}
                <div className="mt-6">
                    <div className="flex items-center gap-2 mb-3">
                        <h3 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                            Ride funnel
                        </h3>
                        <span className="text-[11px] text-muted-foreground">
                            of rides requested this window
                        </span>
                    </div>
                    <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                        <MetricCard icon={Search}        label="Price Searches"        metric={fn?.price_searches}        format={fmtCount} loading={loading} />
                        <MetricCard icon={Car}           label="Rides Requested"       metric={fn?.requested}             format={fmtCount} loading={loading} />
                        <MetricCard icon={Radar}         label="Searched for Driver"   metric={fn?.reached_searching}     format={fmtCount} loading={loading} />
                        <MetricCard icon={Flag}          label="Travelled"             metric={fn?.completed}             format={fmtCount} accent="text-emerald-600 dark:text-emerald-400" loading={loading} />
                        <MetricCard icon={XCircle}       label="Rider Cancelled"       metric={fn?.rider_cancelled}       format={fmtCount} accent="text-amber-600 dark:text-amber-400" loading={loading} />
                        <MetricCard icon={XCircle}       label="Driver Cancelled"      metric={fn?.driver_cancelled}      format={fmtCount} accent="text-red-600 dark:text-red-400" loading={loading} />
                        <MetricCard icon={AlertTriangle} label="Cancelled After Start" metric={fn?.cancelled_after_start} format={fmtCount} accent="text-red-600 dark:text-red-400" loading={loading} />
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
                                    <span className="w-2 h-2 rounded-full bg-amber-500" />
                                    Rider {cx.current.rider}
                                </span>
                                <span className="inline-flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-full bg-red-500" />
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

function CancellationMixBar({ rider, driver, system }: { rider: number; driver: number; system: number }) {
    const total = rider + driver + system;
    if (total === 0) {
        return <p className="text-xs text-muted-foreground">No cancellations in this window.</p>;
    }
    const pct = (n: number) => `${(n / total) * 100}%`;
    return (
        <div className="flex h-3 w-full rounded-md overflow-hidden bg-muted">
            <div className="bg-amber-500" style={{ width: pct(rider) }} title={`${rider} rider-cancelled`} />
            <div className="bg-red-500" style={{ width: pct(driver) }} title={`${driver} driver-cancelled`} />
            <div className="bg-muted-foreground/60" style={{ width: pct(system) }} title={`${system} system / no-driver-found`} />
        </div>
    );
}

// Helpers for time formatting in the payouts header. "1.5 h" reads
// better than "1 h 30 m" at small font sizes, so use decimal hours
// unless the value drops below an hour.
function fmtHours(n: number) {
    if (n <= 0) return "—";
    if (n < 1) return `${Math.round(n * 60)} min`;
    if (n < 24) return `${n.toFixed(1)} h`;
    return `${(n / 24).toFixed(1)} d`;
}

function PayoutsCeoHeader({
    overview,
    loading,
    period,
    onPeriodChange,
    serviceAreaId,
    onServiceAreaChange,
    serviceAreas,
}: {
    overview: PayoutsOverview | null;
    loading: boolean;
    period: EarningsPeriod;
    onPeriodChange: (p: EarningsPeriod) => void;
    serviceAreaId: string;
    onServiceAreaChange: (id: string) => void;
    serviceAreas: Array<{ id: string; name?: string }>;
}) {
    const m = overview?.metrics;
    return (
        <div className="space-y-4">
            <div className="flex items-end justify-between gap-3 flex-wrap">
                <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                        Payout flow
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {overview ? overview.period.label : "Loading…"} · compared to prior {overview?.period.days ?? "—"} days
                    </p>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                    {/* Service-area filter — scopes every payout metric +
                        the daily chart + the operational queues. Independent
                        from the earnings tab's area filter on purpose: the
                        operator may want to look at payouts fleet-wide while
                        looking at earnings for one area. */}
                    <div className="flex items-center gap-1.5">
                        <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                        <Select value={serviceAreaId} onValueChange={onServiceAreaChange}>
                            <SelectTrigger className="h-8 text-xs w-[180px]">
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
                {/* Outstanding payable — the dominant working-capital
                    number for a 0% commission marketplace. Amber accent
                    because high outstanding is operational debt to drivers,
                    not a positive metric. Snapshot value (not period-windowed). */}
                <MetricCard
                    icon={Hourglass}
                    label="Outstanding to drivers"
                    metric={m?.outstanding_payable}
                    format={fmtMoney}
                    accent="text-amber-600 dark:text-amber-400"
                    loading={loading}
                />
                <MetricCard
                    icon={CheckCircle}
                    label="Paid out"
                    metric={m?.total_paid_out}
                    format={fmtMoney}
                    accent="text-emerald-600 dark:text-emerald-400"
                    loading={loading}
                />
                <MetricCard
                    icon={Clock}
                    label="Pending in flight"
                    metric={m?.pending_in_flight}
                    format={fmtMoney}
                    loading={loading}
                />
                <MetricCard
                    icon={AlertTriangle}
                    label="Failed"
                    metric={m?.failed_amount}
                    format={fmtMoney}
                    accent="text-red-600 dark:text-red-400"
                    loading={loading}
                />
                <MetricCard
                    icon={Activity}
                    label="Success rate"
                    metric={m?.success_rate_pct}
                    format={fmtPct}
                    accent="text-emerald-600 dark:text-emerald-400"
                    loading={loading}
                />
                <MetricCard
                    icon={Clock}
                    label="Median time to payout"
                    metric={m?.median_time_to_payout_hours}
                    format={fmtHours}
                    loading={loading}
                />
                <MetricCard
                    icon={DollarSign}
                    label="Avg payout"
                    metric={m?.avg_payout_amount}
                    format={fmtMoney}
                    loading={loading}
                />
                <MetricCard
                    icon={Wallet}
                    label="Payouts"
                    metric={m?.payouts_count}
                    format={fmtCount}
                    loading={loading}
                />
            </div>

            {/* Daily payout volume — stacked bars by status. Failed
                stacks on top in red so a spike in failures is visible
                even when paid_out dominates the y-axis. */}
            <Card className="border-border/50">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm">Daily payout volume</CardTitle>
                </CardHeader>
                <CardContent>
                    {loading || !overview ? (
                        <div className="h-56 w-full bg-muted/30 rounded animate-pulse" />
                    ) : (
                        <ResponsiveContainer width="100%" height={224}>
                            <BarChart data={overview.daily_series} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
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
                                    contentStyle={tooltipStyle}
                                    formatter={(value, name) => {
                                        const n = Number(value ?? 0);
                                        const label =
                                            name === "paid_out" ? "Paid out"
                                            : name === "pending" ? "Pending"
                                            : name === "failed" ? "Failed"
                                            : String(name);
                                        return [fmtMoney(n), label] as [string, string];
                                    }}
                                    labelFormatter={(d) => {
                                        try {
                                            return new Date(d as string).toLocaleDateString(undefined, { weekday: "short", month: "short", day: "numeric" });
                                        } catch { return d as string; }
                                    }}
                                />
                                <Bar dataKey="paid_out" stackId="a" fill="hsl(142 71% 45%)" />
                                <Bar dataKey="pending" stackId="a" fill="hsl(38 92% 50%)" />
                                <Bar dataKey="failed" stackId="a" fill="hsl(0 84% 60%)" />
                            </BarChart>
                        </ResponsiveContainer>
                    )}
                </CardContent>
            </Card>

            {/* Pass 2 — operational queues. Sits below the chart so the
                CEO header reads first, then the operator drills down
                into "what's actually broken". */}
            {overview && <PayoutsOpsQueues overview={overview} />}
        </div>
    );
}

function PayoutsOpsQueues({ overview }: { overview: PayoutsOverview }) {
    const { failure_reasons, stuck_over_48h, blocked_drivers, top_drivers, at_risk_drivers } = overview;
    const hasAnyHealth = stuck_over_48h.count > 0 || blocked_drivers.count > 0 || failure_reasons.length > 0;

    const { sorted: sortedFailureReasons, sort: frSort, toggle: frToggle } = useTableSort(failure_reasons);
    const { sorted: sortedAtRisk, sort: arSort, toggle: arToggle } = useTableSort(at_risk_drivers);
    const { sorted: sortedTopDrivers, sort: tdSort, toggle: tdToggle } = useTableSort(top_drivers);

    return (
        <div className="space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                Operational queues
            </h2>

            {/* Health-summary row. Stuck + Blocked are "right now"
                counters that need intervention; rendered as alert-toned
                cards so a non-zero value reads as a thing to address. */}
            <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                <Card className={`border-border/50 ${stuck_over_48h.count > 0 ? "border-amber-300 dark:border-amber-800" : ""}`}>
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                                <Hourglass className="h-3.5 w-3.5" />
                                Stuck &gt; 48h
                            </div>
                            <span className="text-[10px] text-muted-foreground">manual review</span>
                        </div>
                        <p className={`text-2xl font-bold tabular-nums mt-1.5 ${stuck_over_48h.count > 0 ? "text-amber-600 dark:text-amber-400" : ""}`}>
                            {stuck_over_48h.count}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-1">{fmtMoney(stuck_over_48h.amount)} held up</p>
                    </CardContent>
                </Card>
                <Card className={`border-border/50 ${blocked_drivers.count > 0 ? "border-red-300 dark:border-red-800" : ""}`}>
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                                <XCircle className="h-3.5 w-3.5" />
                                Blocked by Stripe
                            </div>
                            <span className="text-[10px] text-muted-foreground">payouts_enabled=false</span>
                        </div>
                        <p className={`text-2xl font-bold tabular-nums mt-1.5 ${blocked_drivers.count > 0 ? "text-red-600 dark:text-red-400" : ""}`}>
                            {blocked_drivers.count}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-1">{fmtMoney(blocked_drivers.outstanding_balance)} undeliverable</p>
                    </CardContent>
                </Card>
                <Card className="border-border/50">
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                                <AlertTriangle className="h-3.5 w-3.5" />
                                Failure buckets
                            </div>
                            <span className="text-[10px] text-muted-foreground">top reasons</span>
                        </div>
                        <p className="text-2xl font-bold tabular-nums mt-1.5">{failure_reasons.length}</p>
                        <p className="text-[11px] text-muted-foreground mt-1">distinct error groups in window</p>
                    </CardContent>
                </Card>
            </div>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {/* Failure reasons table. Sorted by count desc on the
                    backend; truncates long error strings (already
                    bucketed there) and shows count + amount. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <AlertTriangle className="h-4 w-4 text-red-500" />
                            Why payouts are failing
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-0">
                        {failure_reasons.length === 0 ? (
                            <div className="px-6 py-10 text-center text-muted-foreground text-xs">
                                No failed payouts in this window. {hasAnyHealth ? "" : "Looking good."}
                            </div>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="reason" sort={frSort} onSort={frToggle} className="text-[11px] uppercase tracking-wide h-9">Reason</SortableHead>
                                        <SortableHead column="count" sort={frSort} onSort={frToggle} align="right" className="text-[11px] uppercase tracking-wide h-9">Count</SortableHead>
                                        <SortableHead column="amount" sort={frSort} onSort={frToggle} align="right" className="text-[11px] uppercase tracking-wide h-9">Amount</SortableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sortedFailureReasons.map((r) => (
                                        <TableRow key={r.reason}>
                                            <TableCell className="text-xs font-mono truncate max-w-[280px]" title={r.reason}>{r.reason}</TableCell>
                                            <TableCell className="text-xs text-right tabular-nums font-semibold">{r.count}</TableCell>
                                            <TableCell className="text-xs text-right tabular-nums">{fmtMoney(r.amount)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>

                {/* At-risk drivers — multi-failure list. Sorted by failure
                    count desc on backend. Click navigates to the driver's
                    Payouts tab in the existing slideout via the drivers
                    page's id param. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <UserCheck className="h-4 w-4 text-amber-500" />
                            At-risk drivers
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="p-0">
                        {at_risk_drivers.length === 0 ? (
                            <div className="px-6 py-10 text-center text-muted-foreground text-xs">
                                No drivers with multiple failures in this window.
                            </div>
                        ) : (
                            <Table>
                                <TableHeader>
                                    <TableRow>
                                        <SortableHead column="name" sort={arSort} onSort={arToggle} className="text-[11px] uppercase tracking-wide h-9">Driver</SortableHead>
                                        <SortableHead column="failure_count" sort={arSort} onSort={arToggle} align="right" className="text-[11px] uppercase tracking-wide h-9">Failures</SortableHead>
                                        <SortableHead column="last_reason" sort={arSort} onSort={arToggle} className="text-[11px] uppercase tracking-wide h-9">Last reason</SortableHead>
                                    </TableRow>
                                </TableHeader>
                                <TableBody>
                                    {sortedAtRisk.map((d) => (
                                        <TableRow key={d.driver_id}>
                                            <TableCell className="text-xs">
                                                <a
                                                    href={`/dashboard/drivers?id=${d.driver_id}`}
                                                    className="hover:underline font-medium truncate block max-w-[160px]"
                                                    title={d.name}
                                                >
                                                    {d.name}
                                                </a>
                                            </TableCell>
                                            <TableCell className="text-xs text-right tabular-nums font-semibold text-red-600 dark:text-red-400">
                                                {d.failure_count}
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground font-mono truncate max-w-[220px]" title={d.last_reason ?? ""}>
                                                {d.last_reason ? (d.last_reason.length > 40 ? d.last_reason.slice(0, 40) + "…" : d.last_reason) : "—"}
                                            </TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        )}
                    </CardContent>
                </Card>
            </div>

            {/* Top earning drivers in window — for relationship-management
                and "who do we owe the most this week" context. */}
            <Card className="border-border/50">
                <CardHeader className="pb-2">
                    <CardTitle className="text-sm flex items-center gap-2">
                        <TrendingUp className="h-4 w-4 text-emerald-500" />
                        Top drivers by payout volume
                    </CardTitle>
                </CardHeader>
                <CardContent className="p-0">
                    {top_drivers.length === 0 ? (
                        <div className="px-6 py-10 text-center text-muted-foreground text-xs">
                            No completed payouts in this window.
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9">#</TableHead>
                                    <SortableHead column="name" sort={tdSort} onSort={tdToggle} className="text-[11px] uppercase tracking-wide h-9">Driver</SortableHead>
                                    <SortableHead column="payout_count" sort={tdSort} onSort={tdToggle} align="right" className="text-[11px] uppercase tracking-wide h-9">Payouts</SortableHead>
                                    <SortableHead column="amount" sort={tdSort} onSort={tdToggle} align="right" className="text-[11px] uppercase tracking-wide h-9">Total</SortableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {sortedTopDrivers.map((d, i) => (
                                    <TableRow key={d.driver_id}>
                                        <TableCell className="text-xs text-muted-foreground font-mono">{i + 1}</TableCell>
                                        <TableCell className="text-xs">
                                            <a
                                                href={`/dashboard/drivers?id=${d.driver_id}`}
                                                className="hover:underline font-medium truncate block max-w-[220px]"
                                                title={d.name}
                                            >
                                                {d.name}
                                            </a>
                                        </TableCell>
                                        <TableCell className="text-xs text-right tabular-nums">{d.payout_count}</TableCell>
                                        <TableCell className="text-sm text-right tabular-nums font-semibold">{fmtMoney(d.amount)}</TableCell>
                                    </TableRow>
                                ))}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

// Format a YYYY-MM string for display.
function fmtPeriodKey(key: string): string {
    try {
        const [y, m] = key.split("-").map(Number);
        return new Date(y, (m || 1) - 1, 1).toLocaleString(undefined, { month: "long", year: "numeric" });
    } catch {
        return key;
    }
}

function PayoutsCompliance({ overview, onClosed }: { overview: PayoutsOverview; onClosed: () => Promise<void> | void }) {
    const { t4a_snapshot, period_locks } = overview;
    const { toast } = useToast();
    const now = new Date();
    // Default closure target: the previous calendar month — what
    // finance typically closes once the month wraps.
    const defaultYear = now.getMonth() === 0 ? now.getFullYear() - 1 : now.getFullYear();
    const defaultMonth = now.getMonth() === 0 ? 12 : now.getMonth(); // getMonth is 0-indexed; we want prior month 1-indexed
    const [closeYear, setCloseYear] = useState<number>(defaultYear);
    const [closeMonth, setCloseMonth] = useState<number>(defaultMonth);
    const [closing, setClosing] = useState(false);

    const handleClose = async () => {
        const periodLabel = fmtPeriodKey(`${closeYear}-${String(closeMonth).padStart(2, "0")}`);
        if (!window.confirm(
            `Close ${periodLabel}? This writes an audit-log entry snapshotting every completed payout in that month. ` +
            `Closure is advisory — no payouts are physically locked, but the audit row is permanent.`
        )) return;
        setClosing(true);
        try {
            const res = await closePayoutPeriod(closeYear, closeMonth);
            toast({
                title: `${periodLabel} closed`,
                description: `${res.payout_count} payouts · ${fmtMoney(res.total_amount)} total`,
            });
            await onClosed();
        } catch (e: any) {
            toast({ title: "Close failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setClosing(false);
        }
    };

    const totalBucketed = t4a_snapshot.drivers_with_earnings;

    // Recent closures table — sort the already-sliced (latest 6) rows client-side.
    const { sorted: sortedLocks, sort: lockSort, toggle: lockToggle } = useTableSort(period_locks.slice(0, 6));

    return (
        <div className="space-y-4">
            <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                Compliance &amp; finance
            </h2>

            <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
                {/* T4A snapshot — drivers bucketed against the CRA reporting +
                    GST registration thresholds. The over_30k bucket is the
                    operational lever — those drivers MUST register for GST/HST. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <Receipt className="h-4 w-4 text-muted-foreground" />
                            T4A snapshot · {t4a_snapshot.tax_year}
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <div className="grid grid-cols-2 gap-2 text-xs">
                            <div className="rounded-md bg-muted/30 p-2.5">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">Drivers w/ earnings YTD</p>
                                <p className="text-xl font-bold tabular-nums mt-0.5">{totalBucketed.toLocaleString()}</p>
                            </div>
                            <div className="rounded-md bg-muted/30 p-2.5">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">YTD gross earnings</p>
                                <p className="text-xl font-bold tabular-nums mt-0.5">{fmtMoney(t4a_snapshot.ytd_gross_earnings)}</p>
                            </div>
                        </div>
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9">Bucket</TableHead>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9 text-right">Drivers</TableHead>
                                    <TableHead className="text-[11px] uppercase tracking-wide h-9">CRA implication</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">&lt; $500</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.under_500}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">Below T4A reporting threshold</TableCell>
                                </TableRow>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">$500 – $10k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.from_500_to_10k}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">T4A required</TableCell>
                                </TableRow>
                                <TableRow>
                                    <TableCell className="text-xs font-mono">$10k – $30k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums">{t4a_snapshot.buckets.from_10k_to_30k}</TableCell>
                                    <TableCell className="text-[11px] text-muted-foreground">T4A required · GST elective</TableCell>
                                </TableRow>
                                <TableRow className="bg-amber-50/50 dark:bg-amber-900/10">
                                    <TableCell className="text-xs font-mono font-semibold">≥ $30k</TableCell>
                                    <TableCell className="text-xs text-right tabular-nums font-bold text-amber-700 dark:text-amber-300">{t4a_snapshot.buckets.over_30k}</TableCell>
                                    <TableCell className="text-[11px] text-amber-700 dark:text-amber-300">T4A + GST/HST registration MANDATORY</TableCell>
                                </TableRow>
                            </TableBody>
                        </Table>
                    </CardContent>
                </Card>

                {/* Period close — picks a month, writes an audit_log row.
                    Closure is advisory at this scale, not a hard lock — the
                    log answers "did we sign off on May 2026?" for the
                    accountant without requiring schema changes. */}
                <Card className="border-border/50">
                    <CardHeader className="pb-2">
                        <CardTitle className="text-sm flex items-center gap-2">
                            <CheckCircle className="h-4 w-4 text-muted-foreground" />
                            Period close
                        </CardTitle>
                    </CardHeader>
                    <CardContent className="space-y-3">
                        <p className="text-[11px] text-muted-foreground">
                            Writes an audit-log entry snapshotting every completed payout in the selected month — the
                            accountant&apos;s "we signed off on this period" record. Restricted to finance + super_admin.
                        </p>
                        <div className="flex items-end gap-2 flex-wrap">
                            <div>
                                <Label htmlFor="close-month" className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Month</Label>
                                <select
                                    id="close-month"
                                    className="h-9 text-sm rounded-md border border-input bg-background px-2"
                                    value={closeMonth}
                                    onChange={(e) => setCloseMonth(Number(e.target.value))}
                                >
                                    {Array.from({ length: 12 }).map((_, i) => (
                                        <option key={i + 1} value={i + 1}>
                                            {new Date(2000, i, 1).toLocaleString(undefined, { month: "long" })}
                                        </option>
                                    ))}
                                </select>
                            </div>
                            <div>
                                <Label htmlFor="close-year" className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5 block">Year</Label>
                                <Input
                                    id="close-year"
                                    type="number"
                                    min={2024}
                                    max={now.getFullYear()}
                                    value={closeYear}
                                    onChange={(e) => setCloseYear(Number(e.target.value))}
                                    className="h-9 w-[100px] text-sm"
                                />
                            </div>
                            <Button
                                onClick={handleClose}
                                disabled={closing}
                                size="sm"
                                className="bg-emerald-600 hover:bg-emerald-700 text-white"
                            >
                                <CheckCircle className="h-3.5 w-3.5 mr-1.5" />
                                {closing ? "Closing…" : `Close ${fmtPeriodKey(`${closeYear}-${String(closeMonth).padStart(2, "0")}`)}`}
                            </Button>
                        </div>

                        {period_locks.length > 0 && (
                            <div className="pt-2">
                                <p className="text-[10px] uppercase tracking-wide text-muted-foreground font-semibold mb-1.5">Recent closures</p>
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <SortableHead column="period" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">Period</SortableHead>
                                            <SortableHead column="closed_at" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">Closed at</SortableHead>
                                            <SortableHead column="closed_by" sort={lockSort} onSort={lockToggle} className="text-[11px] uppercase tracking-wide h-9">By</SortableHead>
                                        </TableRow>
                                    </TableHeader>
                                    <TableBody>
                                        {sortedLocks.map((lock) => (
                                            <TableRow key={`${lock.period}-${lock.closed_at}`}>
                                                <TableCell className="text-xs font-medium">{fmtPeriodKey(lock.period)}</TableCell>
                                                <TableCell className="text-xs text-muted-foreground">{formatDate(lock.closed_at)}</TableCell>
                                                <TableCell className="text-xs text-muted-foreground font-mono truncate max-w-[140px]" title={lock.closed_by || ""}>
                                                    {lock.closed_by ? lock.closed_by.slice(0, 12) + "…" : "—"}
                                                </TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </div>
                        )}
                    </CardContent>
                </Card>
            </div>
        </div>
    );
}

function RideEarningsTab() {
    const [rides, setRides] = useState<EarningsRide[]>([]);
    const [ridesLoading, setRidesLoading] = useState(true);
    const [ridesTotal, setRidesTotal] = useState(0);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [period, setPeriod] = useState<EarningsPeriod>("7d");
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [overview, setOverview] = useState<EarningsOverview | null>(null);
    const [overviewLoading, setOverviewLoading] = useState(true);

    useEffect(() => {
        // Service-areas list is small (≤ ~20 rows) and only used to populate
        // the filter dropdown — fetch once, no need to re-fetch on period change.
        getServiceAreas().then((rows) => setServiceAreas(Array.isArray(rows) ? rows : [])).catch(() => {});
    }, []);

    useEffect(() => {
        setOverviewLoading(true);
        getEarningsOverview({
            period,
            service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
        })
            .then(setOverview)
            .catch((e) => console.error('[EarningsOverview] load failed:', e))
            .finally(() => setOverviewLoading(false));
    }, [period, serviceAreaId]);

    useEffect(() => {
        // Transaction table — driven by the date range + area filter.
        // No default period: when both inputs are empty the backend
        // returns last 30d (matches what an operator expects on first
        // open). Re-fetches on every change so the table is always in
        // sync with the filters above it.
        setRidesLoading(true);
        getEarningsRides({
            start_date: dateFrom || undefined,
            end_date: dateTo || undefined,
            service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
            limit: 500,
        })
            .then((res) => {
                setRides(res.rides || []);
                setRidesTotal(res.total ?? (res.rides?.length ?? 0));
            })
            .catch((e) => console.error('[EarningsRides] load failed:', e))
            .finally(() => setRidesLoading(false));
    }, [dateFrom, dateTo, serviceAreaId]);

    // Chart drill-down: clicking a day in the daily GBV chart filters
    // the transaction table to that single day. activeLabel from
    // recharts is the date string from daily_series — pass it straight
    // through into both date inputs.
    const onChartDayClick = (day: string) => {
        if (!day) return;
        setDateFrom(day);
        setDateTo(day);
    };
    const drillDownActive = !!(dateFrom && dateTo && dateFrom === dateTo);
    const clearDrillDown = () => { setDateFrom(""); setDateTo(""); };

    const totals = rides.reduce(
        (acc, r) => ({
            totalFare: acc.totalFare + (r.total_fare || 0),
            driverEarnings: acc.driverEarnings + (r.driver_earnings || 0),
            adminEarnings: acc.adminEarnings + (r.admin_earnings || 0),
            tips: acc.tips + (r.tip_amount || 0),
        }),
        { totalFare: 0, driverEarnings: 0, adminEarnings: 0, tips: 0 }
    );

    // Client-side sort of the already date/area-filtered ride feed.
    const { sorted: sortedRides, sort: ridesSort, toggle: ridesToggle } = useTableSort(rides);

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-2 flex-wrap">
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                    {drillDownActive && (
                        <button
                            type="button"
                            onClick={clearDrillDown}
                            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] rounded-md bg-primary/10 text-primary hover:bg-primary/15 transition-colors"
                            title="Clear chart drill-down"
                        >
                            Drilling into {new Date(dateFrom).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                            <X className="h-3 w-3" />
                        </button>
                    )}
                </div>
                <Button variant="outline" onClick={() => exportToCsv("earnings", rides, [
                    { key: "ride_code", label: "Ride Code" }, { key: "ride_id", label: "Ride ID" },
                    { key: "completed_at", label: "Completed At" }, { key: "status", label: "Status" },
                    { key: "driver_name", label: "Driver" }, { key: "rider_name", label: "Rider" },
                    { key: "total_fare", label: "Total Fare" }, { key: "driver_earnings", label: "Driver Earnings" },
                    { key: "admin_earnings", label: "Platform Revenue" }, { key: "tip_amount", label: "Tip" },
                    { key: "tax_amount", label: "Tax" }, { key: "discount_amount", label: "Discount" },
                    { key: "surge_multiplier", label: "Surge" }, { key: "stripe_charge_id", label: "Stripe Charge ID" },
                    { key: "service_area_id", label: "Service Area ID" },
                ])} disabled={rides.length === 0}>
                    <Download className="mr-2 h-4 w-4" /> Export CSV
                </Button>
            </div>

            {/* CEO row — period-over-period deltas, driven by the new
                /admin/earnings/overview endpoint. Replaces the prior 4-card
                static totals (which had no comparison and no time series).
                The daily GBV chart is click-to-drill-down: clicking a day
                filters the transaction table below to that day's rides. */}
            <CeoMetricsHeader
                overview={overview}
                loading={overviewLoading}
                period={period}
                onPeriodChange={setPeriod}
                serviceAreaId={serviceAreaId}
                onServiceAreaChange={setServiceAreaId}
                serviceAreas={serviceAreas}
                onChartDayClick={onChartDayClick}
            />

            {/* Transaction-level totals from the date-filtered ride feed
                below. Kept separate from the CEO row so the operator can
                still answer "what did this exact custom date range pay?"
                without the period selector overriding it. */}
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Total Fares (custom range)</CardTitle></CardHeader>
                    <CardContent><p className="text-2xl font-bold">{formatCurrency(totals.totalFare)}</p></CardContent></Card>
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Driver Earnings</CardTitle></CardHeader>
                    <CardContent><p className="text-2xl font-bold text-emerald-500">{formatCurrency(totals.driverEarnings)}</p></CardContent></Card>
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Platform Revenue</CardTitle></CardHeader>
                    <CardContent><p className="text-2xl font-bold text-violet-500">{formatCurrency(totals.adminEarnings)}</p></CardContent></Card>
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Tips</CardTitle></CardHeader>
                    <CardContent><p className="text-2xl font-bold text-amber-500">{formatCurrency(totals.tips)}</p></CardContent></Card>
            </div>

            <Card className="border-border/50">
                <CardHeader className="pb-2 flex flex-row items-center justify-between gap-2">
                    <CardTitle className="text-sm">
                        Transactions
                        <span className="text-xs font-normal text-muted-foreground ml-2">
                            {ridesTotal > rides.length ? `${rides.length} of ${ridesTotal}` : `${rides.length} ride${rides.length === 1 ? "" : "s"}`}
                        </span>
                    </CardTitle>
                    {ridesTotal > rides.length && (
                        <span className="text-[11px] text-muted-foreground">
                            Showing newest {rides.length}. Narrow the date range or export to see more.
                        </span>
                    )}
                </CardHeader>
                <CardContent className="p-0">
                    {ridesLoading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <SortableHead column="completed_at" sort={ridesSort} onSort={ridesToggle} className="text-[11px] uppercase tracking-wide">Completed</SortableHead>
                                    <SortableHead column="ride_code" sort={ridesSort} onSort={ridesToggle} className="text-[11px] uppercase tracking-wide">Ride</SortableHead>
                                    <SortableHead column="driver_name" sort={ridesSort} onSort={ridesToggle} className="text-[11px] uppercase tracking-wide">Driver</SortableHead>
                                    <SortableHead column="rider_name" sort={ridesSort} onSort={ridesToggle} className="text-[11px] uppercase tracking-wide">Rider</SortableHead>
                                    <SortableHead column="total_fare" sort={ridesSort} onSort={ridesToggle} align="right" className="text-[11px] uppercase tracking-wide">Fare</SortableHead>
                                    <SortableHead column="driver_earnings" sort={ridesSort} onSort={ridesToggle} align="right" className="text-[11px] uppercase tracking-wide">Driver</SortableHead>
                                    <SortableHead column="admin_earnings" sort={ridesSort} onSort={ridesToggle} align="right" className="text-[11px] uppercase tracking-wide">Platform</SortableHead>
                                    <SortableHead column="tip_amount" sort={ridesSort} onSort={ridesToggle} align="right" className="text-[11px] uppercase tracking-wide">Tip</SortableHead>
                                    <SortableHead column="stripe_charge_id" sort={ridesSort} onSort={ridesToggle} className="text-[11px] uppercase tracking-wide">Stripe</SortableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {sortedRides.length === 0 ? (
                                    <TableRow><TableCell colSpan={9} className="text-center text-muted-foreground py-12">No rides in this date range.</TableCell></TableRow>
                                ) : sortedRides.map((r) => {
                                    const code = r.ride_code ? r.ride_code.toLowerCase() : `#${r.ride_id.slice(0, 8)}`;
                                    return (
                                        <TableRow key={r.ride_id}>
                                            <TableCell className="text-xs whitespace-nowrap">{formatDate(r.completed_at || r.created_at)}</TableCell>
                                            <TableCell>
                                                <button
                                                    type="button"
                                                    onClick={() => navigator.clipboard.writeText(r.ride_code || r.ride_id)}
                                                    className="inline-flex items-center gap-1 text-[11px] font-mono text-muted-foreground hover:text-foreground"
                                                    title={`Copy ${r.ride_code || r.ride_id}`}
                                                >
                                                    {code}
                                                </button>
                                            </TableCell>
                                            <TableCell className="text-xs truncate max-w-[140px]" title={r.driver_name || ""}>{r.driver_name || "—"}</TableCell>
                                            <TableCell className="text-xs truncate max-w-[140px]" title={r.rider_name || ""}>{r.rider_name || "—"}</TableCell>
                                            <TableCell className="text-sm font-medium text-right tabular-nums">{formatCurrency(r.total_fare || 0)}</TableCell>
                                            <TableCell className="text-sm text-right tabular-nums text-emerald-500">{formatCurrency(r.driver_earnings || 0)}</TableCell>
                                            <TableCell className="text-sm text-right tabular-nums text-violet-500">{formatCurrency(r.admin_earnings || 0)}</TableCell>
                                            <TableCell className="text-sm text-right tabular-nums text-amber-500">{r.tip_amount > 0 ? formatCurrency(r.tip_amount) : <span className="text-muted-foreground">—</span>}</TableCell>
                                            <TableCell>
                                                {r.stripe_charge_id ? (
                                                    <button
                                                        type="button"
                                                        onClick={() => navigator.clipboard.writeText(r.stripe_charge_id!)}
                                                        className="inline-flex items-center gap-1 text-[10px] font-mono text-muted-foreground hover:text-foreground"
                                                        title={`Copy ${r.stripe_charge_id}`}
                                                    >
                                                        {r.stripe_charge_id.slice(0, 12)}…
                                                    </button>
                                                ) : (
                                                    <span className="text-[10px] text-muted-foreground">—</span>
                                                )}
                                            </TableCell>
                                        </TableRow>
                                    );
                                })}
                            </TableBody>
                        </Table>
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

// ─── Spinr Pass Revenue Tab ───

const COMPARE_COLORS = ["#10b981", "#8b5cf6", "#f59e0b", "#ef4444", "#3b82f6", "#ec4899"];

function SpinrPassRevenueTab() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [selectedAreas, setSelectedAreas] = useState<string[]>([]);
    const [comparing, setComparing] = useState(false);
    const [compareData, setCompareData] = useState<Record<string, any>>({});
    const [compareLoading, setCompareLoading] = useState(false);

    const loadData = () => {
        setLoading(true);
        const params: any = {};
        if (dateFrom) params.start_date = dateFrom;
        if (dateTo) params.end_date = dateTo;
        if (selectedAreas.length > 0 && !comparing) params.service_area_ids = selectedAreas.join(",");
        getSubscriptionStats(params)
            .then((res) => { setData(res); setServiceAreas(res.service_areas || []); })
            .catch((e) => console.error('[Earnings] subscription stats load failed:', e))
            .finally(() => setLoading(false));
    };

    // Load comparison data for each selected area
    const loadCompareData = async () => {
        if (selectedAreas.length < 2) return;
        setCompareLoading(true);
        const results: Record<string, any> = {};
        for (const areaId of selectedAreas) {
            try {
                const params: any = { service_area_ids: areaId };
                if (dateFrom) params.start_date = dateFrom;
                if (dateTo) params.end_date = dateTo;
                results[areaId] = await getSubscriptionStats(params);
            } catch {}
        }
        setCompareData(results);
        setCompareLoading(false);
    };

    useEffect(() => { loadData(); }, [dateFrom, dateTo, selectedAreas, comparing]);
    useEffect(() => { if (comparing && selectedAreas.length >= 2) loadCompareData(); }, [comparing, selectedAreas, dateFrom, dateTo]);

    const toggleArea = (id: string) => {
        setSelectedAreas(prev => prev.includes(id) ? prev.filter(a => a !== id) : [...prev, id]);
    };

    const stats = data?.stats;
    const transactions = data?.transactions || [];
    const planBreakdown = data?.plan_breakdown || [];
    const revenueChart = data?.charts?.daily_revenue || [];
    const subsChart = data?.charts?.daily_subscribers || [];

    // Client-side sort for the plan-breakdown + transactions tables.
    const { sorted: sortedPlanBreakdown, sort: planSort, toggle: planToggle } = useTableSort<any>(planBreakdown);
    const { sorted: sortedTransactions, sort: txSort, toggle: txToggle } = useTableSort<any>(transactions);

    // Build merged comparison chart data
    const buildCompareChart = (key: "daily_revenue" | "daily_subscribers", valueKey: string) => {
        if (selectedAreas.length < 2) return [];
        const firstArea = compareData[selectedAreas[0]];
        if (!firstArea?.charts?.[key]) return [];
        return firstArea.charts[key].map((d: any, i: number) => {
            const row: any = { date: d.date };
            selectedAreas.forEach(areaId => {
                const areaName = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                row[areaName] = compareData[areaId]?.charts?.[key]?.[i]?.[valueKey] || 0;
            });
            return row;
        });
    };

    return (
        <div className="space-y-6">
            {/* Filters row */}
            <div className="flex flex-wrap items-center gap-2">
                {/* Service Area chips */}
                <MapPin className="h-4 w-4 text-muted-foreground" />
                <div className="flex flex-wrap gap-1.5">
                    {serviceAreas.map(a => (
                        <button key={a.id} onClick={() => toggleArea(a.id)}
                            className={`px-2.5 py-1 rounded-lg text-xs font-semibold border transition ${
                                selectedAreas.includes(a.id) ? "bg-primary text-white border-primary" : "bg-muted text-muted-foreground border-transparent hover:border-border"
                            }`}>{a.name}</button>
                    ))}
                    {selectedAreas.length > 0 && (
                        <button onClick={() => setSelectedAreas([])} className="px-2 py-1 text-xs text-muted-foreground hover:text-foreground"><X className="h-3 w-3 inline" /> Clear</button>
                    )}
                </div>

                {/* Compare toggle */}
                {selectedAreas.length >= 2 && (
                    <button onClick={() => setComparing(!comparing)}
                        className={`flex items-center gap-1 px-3 py-1.5 rounded-lg text-xs font-semibold border transition ${
                            comparing ? "bg-violet-500 text-white border-violet-500" : "text-muted-foreground border-border hover:bg-muted"
                        }`}>
                        <GitCompareArrows className="h-3.5 w-3.5" /> Compare
                    </button>
                )}

                <div className="flex-1" />

                {/* Date + Export */}
                <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                <span className="text-muted-foreground text-sm">to</span>
                <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                <Button variant="outline" size="sm" onClick={() => exportToCsv("spinr-pass-transactions", transactions, [
                    { key: "driver_name", label: "Driver" }, { key: "plan_name", label: "Plan" },
                    { key: "price", label: "Amount" }, { key: "status", label: "Status" },
                    { key: "started_at", label: "Started" }, { key: "expires_at", label: "Expires" },
                    { key: "created_at", label: "Transaction Date" },
                ])} disabled={transactions.length === 0}>
                    <Download className="mr-2 h-4 w-4" /> Export
                </Button>
            </div>

            {loading ? (
                <div className="flex items-center justify-center py-20">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                </div>
            ) : !stats ? (
                <div className="text-center py-16 text-muted-foreground">Failed to load subscription stats</div>
            ) : (
                <>
                    {/* Stats Cards */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-4">
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><Users className="h-4 w-4" /> Total Subscribers</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold">{stats.total_subscribers}</p>
                                <p className="text-xs text-muted-foreground mt-1">
                                    <span className="text-emerald-500 font-semibold">{stats.active} active</span> · {stats.expired} expired · {stats.cancelled} cancelled
                                </p>
                            </CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><DollarSign className="h-4 w-4" /> Total Revenue</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold text-emerald-500">{formatCurrency(stats.total_revenue)}</p></CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><TrendingUp className="h-4 w-4" /> Active MRR</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold text-violet-500">{formatCurrency(stats.active_mrr)}</p></CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><CreditCard className="h-4 w-4" /> Period Revenue</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold text-amber-500">{formatCurrency(stats.range_revenue)}</p>
                                <p className="text-xs text-muted-foreground mt-1">{stats.range_transactions} transactions</p>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Comparison Charts */}
                    {comparing && selectedAreas.length >= 2 ? (
                        compareLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <div className="h-8 w-8 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" />
                                <span className="ml-3 text-sm text-muted-foreground">Loading comparison...</span>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                <Card className="border-violet-200 dark:border-violet-800">
                                    <CardHeader className="pb-2">
                                        <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                            <GitCompareArrows className="h-4 w-4 text-violet-500" /> Revenue Comparison
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <ResponsiveContainer width="100%" height={220}>
                                            <LineChart data={buildCompareChart("daily_revenue", "amount")}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
                                                <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} formatter={(v: any) => formatCurrency(Number(v || 0))} />
                                                <Legend />
                                                {selectedAreas.map((areaId, i) => {
                                                    const name = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                                                    return <Line key={areaId} type="monotone" dataKey={name} stroke={COMPARE_COLORS[i % COMPARE_COLORS.length]} strokeWidth={2} dot={{ r: 2 }} />;
                                                })}
                                            </LineChart>
                                        </ResponsiveContainer>
                                    </CardContent>
                                </Card>
                                <Card className="border-violet-200 dark:border-violet-800">
                                    <CardHeader className="pb-2">
                                        <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                            <GitCompareArrows className="h-4 w-4 text-violet-500" /> Subscribers Comparison
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <ResponsiveContainer width="100%" height={220}>
                                            <BarChart data={buildCompareChart("daily_subscribers", "count")}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                                                <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} />
                                                <Legend />
                                                {selectedAreas.map((areaId, i) => {
                                                    const name = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                                                    return <Bar key={areaId} dataKey={name} fill={COMPARE_COLORS[i % COMPARE_COLORS.length]} radius={[3, 3, 0, 0]} />;
                                                })}
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </CardContent>
                                </Card>

                                {/* Comparison summary table */}
                                <Card className="border-violet-200 dark:border-violet-800 lg:col-span-2">
                                    <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold">Area Comparison Summary</CardTitle></CardHeader>
                                    <CardContent className="p-0">
                                        <Table>
                                            <TableHeader>
                                                <TableRow>
                                                    <TableHead>Area</TableHead>
                                                    <TableHead className="text-right">Subscribers</TableHead>
                                                    <TableHead className="text-right">Active</TableHead>
                                                    <TableHead className="text-right">Revenue</TableHead>
                                                    <TableHead className="text-right">MRR</TableHead>
                                                </TableRow>
                                            </TableHeader>
                                            <TableBody>
                                                {selectedAreas.map((areaId, i) => {
                                                    const s = compareData[areaId]?.stats;
                                                    const name = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                                                    return (
                                                        <TableRow key={areaId}>
                                                            <TableCell className="font-semibold">
                                                                <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ backgroundColor: COMPARE_COLORS[i % COMPARE_COLORS.length] }} />
                                                                {name}
                                                            </TableCell>
                                                            <TableCell className="text-right">{s?.total_subscribers || 0}</TableCell>
                                                            <TableCell className="text-right text-emerald-500 font-medium">{s?.active || 0}</TableCell>
                                                            <TableCell className="text-right font-semibold">{formatCurrency(s?.total_revenue || 0)}</TableCell>
                                                            <TableCell className="text-right text-violet-500 font-medium">{formatCurrency(s?.active_mrr || 0)}</TableCell>
                                                        </TableRow>
                                                    );
                                                })}
                                            </TableBody>
                                        </Table>
                                    </CardContent>
                                </Card>
                            </div>
                        )
                    ) : (
                        /* Normal (non-comparison) Charts */
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                            <Card className="border-border/50">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                        <DollarSign className="h-4 w-4 text-emerald-500" /> Daily Subscription Revenue
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {revenueChart.length > 0 ? (
                                        <ResponsiveContainer width="100%" height={200}>
                                            <LineChart data={revenueChart}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
                                                <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} formatter={(v: any) => [formatCurrency(Number(v || 0)), "Revenue"]} />
                                                <Line type="monotone" dataKey="amount" stroke="#10b981" strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                                            </LineChart>
                                        </ResponsiveContainer>
                                    ) : <p className="text-sm text-muted-foreground py-8 text-center">No revenue data in this range</p>}
                                </CardContent>
                            </Card>
                            <Card className="border-border/50">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                        <UserPlus className="h-4 w-4 text-violet-500" /> New Subscribers Per Day
                                    </CardTitle>
                                </CardHeader>
                                <CardContent>
                                    {subsChart.length > 0 ? (
                                        <ResponsiveContainer width="100%" height={200}>
                                            <BarChart data={subsChart} barSize={18}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                                                <Tooltip contentStyle={tooltipStyle} labelStyle={{ fontWeight: 600 }} />
                                                <Bar dataKey="count" name="Subscribers" fill="#8b5cf6" radius={[4, 4, 0, 0]} />
                                            </BarChart>
                                        </ResponsiveContainer>
                                    ) : <p className="text-sm text-muted-foreground py-8 text-center">No subscriber data in this range</p>}
                                </CardContent>
                            </Card>
                        </div>
                    )}

                    {/* Plan Breakdown */}
                    {planBreakdown.length > 0 && (
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm font-semibold">Plan Performance</CardTitle></CardHeader>
                            <CardContent className="p-0">
                                <Table>
                                    <TableHeader><TableRow>
                                        <SortableHead column="name" sort={planSort} onSort={planToggle}>Plan</SortableHead><SortableHead column="count" sort={planSort} onSort={planToggle} align="right">Subscribers</SortableHead>
                                        <SortableHead column="active" sort={planSort} onSort={planToggle} align="right">Active</SortableHead><SortableHead column="revenue" sort={planSort} onSort={planToggle} align="right">Revenue</SortableHead>
                                    </TableRow></TableHeader>
                                    <TableBody>
                                        {sortedPlanBreakdown.map((p: any) => (
                                            <TableRow key={p.plan_id}>
                                                <TableCell className="font-semibold">{p.name}</TableCell>
                                                <TableCell className="text-right">{p.count}</TableCell>
                                                <TableCell className="text-right text-emerald-500 font-medium">{p.active}</TableCell>
                                                <TableCell className="text-right font-semibold">{formatCurrency(p.revenue)}</TableCell>
                                            </TableRow>
                                        ))}
                                    </TableBody>
                                </Table>
                            </CardContent>
                        </Card>
                    )}

                    {/* Transactions Table */}
                    <Card className="border-border/50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-semibold">Subscription Transactions ({transactions.length})</CardTitle>
                        </CardHeader>
                        <CardContent className="p-0">
                            <Table>
                                <TableHeader><TableRow>
                                    <SortableHead column="driver_name" sort={txSort} onSort={txToggle}>Driver</SortableHead><SortableHead column="plan_name" sort={txSort} onSort={txToggle}>Plan</SortableHead><SortableHead column="price" sort={txSort} onSort={txToggle}>Amount</SortableHead>
                                    <SortableHead column="status" sort={txSort} onSort={txToggle}>Status</SortableHead><SortableHead column="started_at" sort={txSort} onSort={txToggle}>Started</SortableHead><SortableHead column="expires_at" sort={txSort} onSort={txToggle}>Expires</SortableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {sortedTransactions.length === 0 ? (
                                        <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-12">No subscription transactions in this period.</TableCell></TableRow>
                                    ) : sortedTransactions.map((t: any) => (
                                        <TableRow key={t.id}>
                                            <TableCell className="font-medium">{t.driver_name}</TableCell>
                                            <TableCell>{t.plan_name}</TableCell>
                                            <TableCell className="font-semibold text-emerald-500">{formatCurrency(t.price)}</TableCell>
                                            <TableCell>
                                                <Badge variant="secondary" className={
                                                    t.status === "active" ? "bg-emerald-500/15 text-emerald-700 dark:text-emerald-400" :
                                                    t.status === "expired" ? "bg-amber-500/15 text-amber-700 dark:text-amber-400" :
                                                    "bg-red-500/15 text-red-700 dark:text-red-400"
                                                }>{t.status}</Badge>
                                            </TableCell>
                                            <TableCell className="text-xs text-muted-foreground">{formatDate(t.started_at)}</TableCell>
                                            <TableCell className="text-xs text-muted-foreground">{formatDate(t.expires_at)}</TableCell>
                                        </TableRow>
                                    ))}
                                </TableBody>
                            </Table>
                        </CardContent>
                    </Card>
                </>
            )}
        </div>
    );
}


// ─── Payouts Tab ───

function PayoutsTab() {
    const [payouts, setPayouts] = useState<any[]>([]);
    const [stats, setStats] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [statusFilter, setStatusFilter] = useState("all");
    const [period, setPeriod] = useState<EarningsPeriod>("7d");
    // Independent from the earnings tab's area filter — operators may
    // want to view payouts fleet-wide while looking at earnings for
    // one area, so each tab tracks its own selection.
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [overview, setOverview] = useState<PayoutsOverview | null>(null);
    const [overviewLoading, setOverviewLoading] = useState(true);
    const [retryingId, setRetryingId] = useState<string | null>(null);
    const [bulkRetrying, setBulkRetrying] = useState(false);
    const { toast } = useToast();

    useEffect(() => {
        // Service areas list is small and only feeds the dropdown —
        // fetch once on mount, no need to refetch per period change.
        getServiceAreas().then((rows) => setServiceAreas(Array.isArray(rows) ? rows : [])).catch(() => {});
    }, []);

    const refreshAll = async () => {
        setLoading(true);
        setOverviewLoading(true);
        try {
            const [p, s, o] = await Promise.all([
                getPayouts().catch(() => []),
                getPayoutStats().catch(() => null),
                getPayoutsOverview({
                    period,
                    service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
                }).catch(() => null),
            ]);
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s);
            if (o) setOverview(o);
        } finally {
            setLoading(false);
            setOverviewLoading(false);
        }
    };

    const handleRowRetry = async (id: string) => {
        setRetryingId(id);
        try {
            await retryPayout(id);
            toast({ title: "Retry queued", description: "Payout flipped back to pending — the retry loop will pick it up." });
            await refreshAll();
        } catch (e: any) {
            toast({ title: "Retry failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setRetryingId(null);
        }
    };

    const handleBulkRetry = async () => {
        if (!overview) return;
        // Use the window the operator is already looking at. since=start
        // matches the Pass 1 daily chart so "retry everything failed in
        // this window" reads as a single coherent action.
        const since = overview.period.start;
        if (!window.confirm(
            `Retry every failed/cancelled payout since ${new Date(since).toLocaleString()}? Each will be flipped to pending and the retry loop will pick them up.`
        )) return;
        setBulkRetrying(true);
        try {
            const res = await bulkRetryPayouts({ since, max_to_retry: 200 });
            toast({
                title: "Bulk retry queued",
                description: `${res.retried} queued · ${res.skipped} skipped · ${res.failed_to_initiate} errored.`,
            });
            await refreshAll();
        } catch (e: any) {
            toast({ title: "Bulk retry failed", description: e?.message || "Unknown error", variant: "destructive" });
        } finally {
            setBulkRetrying(false);
        }
    };

    const handleExportCsv = () => {
        exportToCsv("payouts", payouts, [
            { key: "id", label: "Payout ID" },
            { key: "driver_id", label: "Driver ID" },
            { key: "driver_name", label: "Driver" },
            { key: "amount", label: "Amount" },
            { key: "status", label: "Status" },
            { key: "bank_name", label: "Bank" },
            { key: "account_last4", label: "Account Last 4" },
            { key: "stripe_payout_id", label: "Stripe Payout ID" },
            { key: "error_message", label: "Error" },
            { key: "created_at", label: "Requested" },
            { key: "processed_at", label: "Settled" },
        ]);
    };

    useEffect(() => {
        Promise.all([
            getPayouts().catch(() => []),
            getPayoutStats().catch(() => null),
        ]).then(([p, s]) => {
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s);
        }).finally(() => setLoading(false));
    }, []);

    useEffect(() => {
        setOverviewLoading(true);
        getPayoutsOverview({
            period,
            service_area_id: serviceAreaId !== "all" ? serviceAreaId : undefined,
        })
            .then(setOverview)
            .catch((e) => console.error('[PayoutsOverview] load failed:', e))
            .finally(() => setOverviewLoading(false));
    }, [period, serviceAreaId]);

    const filtered = statusFilter === "all" ? payouts : payouts.filter(p => p.status === statusFilter);

    // Client-side sort of the already status-filtered payout list.
    const { sorted: sortedFiltered, sort: payoutsSort, toggle: payoutsToggle } = useTableSort<any>(filtered);

    const statusBadge = (s: string) => {
        if (s === "completed") return "bg-emerald-500/15 text-emerald-600";
        if (s === "pending") return "bg-amber-500/15 text-amber-600";
        if (s === "failed") return "bg-red-500/15 text-red-600";
        return "bg-zinc-500/15 text-zinc-600";
    };

    return (
        <div className="space-y-6">
            {/* Pass 1 — CEO header. Sits above the legacy stat cards
                so the page opens to "is payout flow healthy?" instead
                of a static transaction log. Skeleton-loads independently
                so it doesn't block the rest of the page. */}
            <PayoutsCeoHeader
                overview={overview}
                loading={overviewLoading}
                period={period}
                onPeriodChange={setPeriod}
                serviceAreaId={serviceAreaId}
                onServiceAreaChange={setServiceAreaId}
                serviceAreas={serviceAreas}
            />

            {/* Legacy stats — kept for backward compatibility while the
                Pass 1 header takes over the headline role. Will retire
                once Pass 2 lands. */}
            {loading ? (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4 animate-pulse">
                    {[0, 1, 2, 3].map((i) => <div key={i} className="h-20 rounded-xl bg-muted" />)}
                </div>
            ) : stats && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-green-500" /> Total Paid</div>
                        <div className="text-2xl font-bold text-green-600">${stats.total_paid?.toLocaleString()}</div>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-amber-500" /> Pending</div>
                        <div className="text-2xl font-bold text-amber-600">${stats.total_pending?.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">{stats.pending_count} payouts</p>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><AlertTriangle className="h-4 w-4 text-red-500" /> Failed</div>
                        <div className="text-2xl font-bold text-red-600">${stats.total_failed?.toLocaleString()}</div>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Wallet className="h-4 w-4" /> Total Payouts</div>
                        <div className="text-2xl font-bold">{stats.payout_count}</div>
                    </CardContent></Card>
                </div>
            )}

            {/* Filter + Table */}
            <Card>
                <CardHeader className="flex flex-row items-center justify-between gap-3 flex-wrap">
                    <CardTitle>Payout History</CardTitle>
                    <div className="flex items-center gap-2 flex-wrap">
                        <div className="flex gap-1 bg-muted rounded-lg p-0.5">
                            {["all", "pending", "completed", "failed"].map(s => (
                                <button key={s} onClick={() => setStatusFilter(s)}
                                    className={`px-3 py-1 rounded-md text-xs font-medium transition ${statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"}`}>
                                    {s.charAt(0).toUpperCase() + s.slice(1)}
                                </button>
                            ))}
                        </div>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleBulkRetry}
                            disabled={bulkRetrying || !overview}
                            title="Retry every failed/cancelled payout in the selected period"
                        >
                            <Undo2 className="h-3.5 w-3.5 mr-1.5" />
                            {bulkRetrying ? "Retrying…" : "Bulk retry failed"}
                        </Button>
                        <Button
                            variant="outline"
                            size="sm"
                            onClick={handleExportCsv}
                            disabled={payouts.length === 0}
                        >
                            <Download className="h-3.5 w-3.5 mr-1.5" />
                            Export CSV
                        </Button>
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <SortableHead column="driver_name" sort={payoutsSort} onSort={payoutsToggle}>Driver</SortableHead>
                                <SortableHead column="amount" sort={payoutsSort} onSort={payoutsToggle}>Amount</SortableHead>
                                <SortableHead column="status" sort={payoutsSort} onSort={payoutsToggle}>Status</SortableHead>
                                <SortableHead column="bank_name" sort={payoutsSort} onSort={payoutsToggle}>Bank</SortableHead>
                                <SortableHead column="created_at" sort={payoutsSort} onSort={payoutsToggle}>Requested</SortableHead>
                                <TableHead className="text-right">Action</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {sortedFiltered.length === 0 ? (
                                <TableRow><TableCell colSpan={6} className="text-center py-8 text-muted-foreground">No payouts found</TableCell></TableRow>
                            ) : sortedFiltered.map((p: any) => {
                                const isRetryable = p.status === "failed" || p.status === "cancelled";
                                const isRetrying = retryingId === p.id;
                                return (
                                    <TableRow key={p.id}>
                                        <TableCell className="font-medium">{p.driver_name || "Unknown"}</TableCell>
                                        <TableCell className="font-mono font-bold">${Number(p.amount || 0).toFixed(2)}</TableCell>
                                        <TableCell>
                                            <Badge className={statusBadge(p.status)}>{p.status}</Badge>
                                            {p.error_message && (
                                                <p className="text-[10px] text-red-600 dark:text-red-400 mt-0.5 truncate max-w-[200px]" title={p.error_message}>
                                                    {p.error_message}
                                                </p>
                                            )}
                                        </TableCell>
                                        <TableCell className="text-sm text-muted-foreground">{p.bank_name || "—"} {p.account_last4 ? `•••${p.account_last4}` : ""}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{formatDate(p.created_at)}</TableCell>
                                        <TableCell className="text-right">
                                            {isRetryable ? (
                                                <Button
                                                    size="xs"
                                                    variant="outline"
                                                    className="h-7 text-[11px]"
                                                    onClick={() => handleRowRetry(p.id)}
                                                    disabled={isRetrying}
                                                >
                                                    <Undo2 className="h-3 w-3 mr-1" />
                                                    {isRetrying ? "Retrying…" : "Retry"}
                                                </Button>
                                            ) : (
                                                <span className="text-[10px] text-muted-foreground">—</span>
                                            )}
                                        </TableCell>
                                    </TableRow>
                                );
                            })}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>

            {/* Pass 4 — compliance. T4A snapshot + period close. Sits
                below the transaction table so finance opens the page
                lower to find these; day-to-day operators don't see
                them on first glance. */}
            {overview && (
                <PayoutsCompliance overview={overview} onClosed={refreshAll} />
            )}
        </div>
    );
}
