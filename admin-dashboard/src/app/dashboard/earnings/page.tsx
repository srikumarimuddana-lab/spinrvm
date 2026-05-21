"use client";

import { useEffect, useState } from "react";
import { getEarnings, getEarningsOverview, getServiceAreas, getSubscriptionStats, type EarningsOverview, type EarningsPeriod, type MetricWithDelta } from "@/lib/api";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Download, Car, CreditCard, Users, TrendingUp, TrendingDown, DollarSign, UserPlus, Clock, MapPin, X, GitCompareArrows, Wallet, CheckCircle, AlertTriangle, Percent, Receipt, UserCheck, XCircle, Ticket, Zap, Landmark, Undo2, Filter } from "lucide-react";
import { getPayouts, getPayoutStats } from "@/lib/api";
import { useRequireModule } from "@/hooks/useRequireModule";
import { Legend } from "recharts";
import { Input } from "@/components/ui/input";
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
    const [tab, setTab] = useState<"rides" | "spinr-pass" | "payouts">("rides");

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
            </div>

            {tab === "rides" && <RideEarningsTab />}
            {tab === "spinr-pass" && <SpinrPassRevenueTab />}
            {tab === "payouts" && <PayoutsTab />}
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
}: {
    overview: EarningsOverview | null;
    loading: boolean;
    period: EarningsPeriod;
    onPeriodChange: (p: EarningsPeriod) => void;
    serviceAreaId: string;
    onServiceAreaChange: (id: string) => void;
    serviceAreas: Array<{ id: string; name?: string }>;
}) {
    const m = overview?.metrics;
    const cx = overview?.cancellation_breakdown;
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
                            <LineChart data={overview.daily_series} margin={{ top: 10, right: 16, left: -8, bottom: 0 }}>
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

function RideEarningsTab() {
    const [earnings, setEarnings] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");
    const [period, setPeriod] = useState<EarningsPeriod>("7d");
    const [serviceAreaId, setServiceAreaId] = useState<string>("all");
    const [serviceAreas, setServiceAreas] = useState<Array<{ id: string; name?: string }>>([]);
    const [overview, setOverview] = useState<EarningsOverview | null>(null);
    const [overviewLoading, setOverviewLoading] = useState(true);

    useEffect(() => {
        getEarnings()
            .then((data) => {
                if (Array.isArray(data)) { setEarnings(data); }
                else if (data && typeof data === "object") {
                    const arr = (data as any).earnings || (data as any).rides || (data as any).data;
                    setEarnings(Array.isArray(arr) ? arr : []);
                } else { setEarnings([]); }
            })
            .catch((e) => console.error('[Earnings] load failed:', e))
            .finally(() => setLoading(false));
    }, []);

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

    const filtered = earnings.filter((e) => {
        if (!dateFrom && !dateTo) return true;
        const d = e.date ? new Date(e.date).toISOString().split("T")[0] : "";
        if (dateFrom && d < dateFrom) return false;
        if (dateTo && d > dateTo) return false;
        return true;
    });

    const totals = filtered.reduce(
        (acc, e) => ({
            totalFare: acc.totalFare + (e.total_fare || 0),
            driverEarnings: acc.driverEarnings + (e.driver_earnings || 0),
            adminEarnings: acc.adminEarnings + (e.admin_earnings || 0),
            tips: acc.tips + (e.tip_amount || 0),
        }),
        { totalFare: 0, driverEarnings: 0, adminEarnings: 0, tips: 0 }
    );

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-end gap-2">
                <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                <span className="text-muted-foreground text-sm">to</span>
                <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                <Button variant="outline" onClick={() => exportToCsv("earnings", filtered, [
                    { key: "ride_id", label: "Ride ID" }, { key: "status", label: "Status" },
                    { key: "total_fare", label: "Total Fare" }, { key: "driver_earnings", label: "Driver Earnings" },
                    { key: "admin_earnings", label: "Platform Revenue" }, { key: "tip_amount", label: "Tip" },
                    { key: "stripe_transaction_id", label: "Stripe Transaction ID" }, { key: "date", label: "Date" },
                ])} disabled={filtered.length === 0}>
                    <Download className="mr-2 h-4 w-4" /> Export CSV
                </Button>
            </div>

            {/* CEO row — period-over-period deltas, driven by the new
                /admin/earnings/overview endpoint. Replaces the prior 4-card
                static totals (which had no comparison and no time series). */}
            <CeoMetricsHeader
                overview={overview}
                loading={overviewLoading}
                period={period}
                onPeriodChange={setPeriod}
                serviceAreaId={serviceAreaId}
                onServiceAreaChange={setServiceAreaId}
                serviceAreas={serviceAreas}
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
                <CardContent className="p-0">
                    {loading ? (
                        <div className="flex items-center justify-center p-12">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        </div>
                    ) : (
                        <Table>
                            <TableHeader>
                                <TableRow>
                                    <TableHead>Ride ID</TableHead><TableHead>Status</TableHead>
                                    <TableHead>Total Fare</TableHead><TableHead>Driver</TableHead>
                                    <TableHead>Platform</TableHead><TableHead>Tip</TableHead><TableHead>Date</TableHead>
                                </TableRow>
                            </TableHeader>
                            <TableBody>
                                {filtered.length === 0 ? (
                                    <TableRow><TableCell colSpan={7} className="text-center text-muted-foreground py-12">No earnings data yet.</TableCell></TableRow>
                                ) : filtered.map((e) => (
                                    <TableRow key={e.ride_id}>
                                        <TableCell className="font-mono text-xs">{e.ride_id?.slice(0, 8)}...</TableCell>
                                        <TableCell><Badge variant="secondary" className={statusColor(e.status)}>{e.status?.replace(/_/g, " ")}</Badge></TableCell>
                                        <TableCell>{formatCurrency(e.total_fare || 0)}</TableCell>
                                        <TableCell className="text-emerald-500">{formatCurrency(e.driver_earnings || 0)}</TableCell>
                                        <TableCell className="text-violet-500">{formatCurrency(e.admin_earnings || 0)}</TableCell>
                                        <TableCell className="text-amber-500">{formatCurrency(e.tip_amount || 0)}</TableCell>
                                        <TableCell className="text-xs text-muted-foreground">{formatDate(e.date)}</TableCell>
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
                                        <TableHead>Plan</TableHead><TableHead className="text-right">Subscribers</TableHead>
                                        <TableHead className="text-right">Active</TableHead><TableHead className="text-right">Revenue</TableHead>
                                    </TableRow></TableHeader>
                                    <TableBody>
                                        {planBreakdown.map((p: any) => (
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
                                    <TableHead>Driver</TableHead><TableHead>Plan</TableHead><TableHead>Amount</TableHead>
                                    <TableHead>Status</TableHead><TableHead>Started</TableHead><TableHead>Expires</TableHead>
                                </TableRow></TableHeader>
                                <TableBody>
                                    {transactions.length === 0 ? (
                                        <TableRow><TableCell colSpan={6} className="text-center text-muted-foreground py-12">No subscription transactions in this period.</TableCell></TableRow>
                                    ) : transactions.map((t: any) => (
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

    useEffect(() => {
        Promise.all([
            getPayouts().catch(() => []),
            getPayoutStats().catch(() => null),
        ]).then(([p, s]) => {
            setPayouts(Array.isArray(p) ? p : []);
            setStats(s);
        }).finally(() => setLoading(false));
    }, []);

    const filtered = statusFilter === "all" ? payouts : payouts.filter(p => p.status === statusFilter);

    const statusBadge = (s: string) => {
        if (s === "completed") return "bg-emerald-500/15 text-emerald-600";
        if (s === "pending") return "bg-amber-500/15 text-amber-600";
        if (s === "failed") return "bg-red-500/15 text-red-600";
        return "bg-zinc-500/15 text-zinc-600";
    };

    if (loading) return <div className="flex justify-center p-12"><div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" /></div>;

    return (
        <div className="space-y-6">
            {/* Stats */}
            {stats && (
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
                <CardHeader className="flex flex-row items-center justify-between">
                    <CardTitle>Payout History</CardTitle>
                    <div className="flex gap-1 bg-muted rounded-lg p-0.5">
                        {["all", "pending", "completed", "failed"].map(s => (
                            <button key={s} onClick={() => setStatusFilter(s)}
                                className={`px-3 py-1 rounded-md text-xs font-medium transition ${statusFilter === s ? "bg-background shadow-sm" : "text-muted-foreground"}`}>
                                {s.charAt(0).toUpperCase() + s.slice(1)}
                            </button>
                        ))}
                    </div>
                </CardHeader>
                <CardContent className="p-0">
                    <Table>
                        <TableHeader>
                            <TableRow>
                                <TableHead>Driver</TableHead>
                                <TableHead>Amount</TableHead>
                                <TableHead>Status</TableHead>
                                <TableHead>Bank</TableHead>
                                <TableHead>Requested</TableHead>
                            </TableRow>
                        </TableHeader>
                        <TableBody>
                            {filtered.length === 0 ? (
                                <TableRow><TableCell colSpan={5} className="text-center py-8 text-muted-foreground">No payouts found</TableCell></TableRow>
                            ) : filtered.map((p: any) => (
                                <TableRow key={p.id}>
                                    <TableCell className="font-medium">{p.driver_name || "Unknown"}</TableCell>
                                    <TableCell className="font-mono font-bold">${Number(p.amount || 0).toFixed(2)}</TableCell>
                                    <TableCell><Badge className={statusBadge(p.status)}>{p.status}</Badge></TableCell>
                                    <TableCell className="text-sm text-muted-foreground">{p.bank_name || "—"} {p.account_last4 ? `•••${p.account_last4}` : ""}</TableCell>
                                    <TableCell className="text-xs text-muted-foreground">{formatDate(p.created_at)}</TableCell>
                                </TableRow>
                            ))}
                        </TableBody>
                    </Table>
                </CardContent>
            </Card>
        </div>
    );
}
