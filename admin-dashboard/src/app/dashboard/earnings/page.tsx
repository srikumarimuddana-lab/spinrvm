"use client";

import { useEffect, useState } from "react";
import { getEarnings, getEarningsOverview, getEarningsRides, getServiceAreas, getSubscriptionStats, type EarningsOverview, type EarningsPeriod, type EarningsRide } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import ReferralsPanel from "@/components/referrals-panel";
import { PageHeader } from "@/components/page-header";
import AutoPayoutsPanel from "@/components/auto-payouts-panel";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Download, Car, CreditCard, Users, TrendingUp, DollarSign, UserPlus, Clock, MapPin, X, GitCompareArrows, Wallet, CheckCircle, AlertTriangle, Undo2, Gift, CalendarClock } from "lucide-react";
import { getPayouts, getPayoutStats, getPayoutsOverview, retryPayout, bulkRetryPayouts, type PayoutsOverview } from "@/lib/api";
import { useToast } from "@/components/ui/use-toast";
import { useRequireModule } from "@/hooks/useRequireModule";
import { useAuthStore } from "@/store/authStore";
import { Legend } from "recharts";
import { Input } from "@/components/ui/input";
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useTheme } from "next-themes";
import { chartColors } from "@/components/analytics/chart-palette";
import { CeoMetricsHeader } from "./_components/ceo-metrics-header";
import { PayoutsCeoHeader } from "./_components/payouts-ceo-header";
import { PayoutsCompliance } from "./_components/payouts-compliance";

export default function EarningsPage() {
    const { allowed } = useRequireModule("earnings");
    const [tab, setTab] = useState<"rides" | "spinr-pass" | "payouts" | "auto-payouts" | "referrals">("rides");

    // The Referrals tab calls /api/admin/referrals/leaderboard, which is gated by
    // the `drivers` module on the backend. Only show it to admins who actually
    // have drivers access — otherwise they'd see the tab and hit a 403.
    // Corporate + admin portal review, Admin #4: role === "admin" alone
    // does NOT grant drivers access on the backend (only super_admin
    // bypasses require_module), so it must not grant it here either —
    // the previous check let exactly the 403 this comment warns about
    // happen for any admin-role user without the drivers module.
    const user = useAuthStore((s) => s.user);
    const canSeeReferrals =
        user?.role === "super_admin" || (user?.modules ?? []).includes("drivers");

    if (!allowed) return null;
    return (
        <div className="space-y-6">
            <PageHeader
                title="Earnings & Payouts"
                description="Platform revenue, subscriptions, and driver payouts"
            />

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
                <button onClick={() => setTab("auto-payouts")}
                    className={`flex items-center gap-1.5 px-5 py-2 rounded-lg text-sm font-semibold transition ${tab === "auto-payouts" ? "bg-background text-foreground shadow-sm" : "text-muted-foreground"}`}>
                    <CalendarClock className="h-4 w-4" /> Weekly Payouts
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
            {/* Spinr-controlled Sunday batch: run history + who is blocked
                right now. Same `earnings` module gate as this page. */}
            {tab === "auto-payouts" && <div className="pt-1"><AutoPayoutsPanel /></div>}
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
                    <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" aria-label="Start date" />
                    <span className="text-muted-foreground text-sm">to</span>
                    <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" aria-label="End date" />
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
                    {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (driver earnings), not a status signal (#2816) */}
                    <CardContent><p className="text-2xl font-bold text-emerald-500">{formatCurrency(totals.driverEarnings)}</p></CardContent></Card>
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Platform Revenue</CardTitle></CardHeader>
                    {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (platform revenue), not a status signal (#2816) */}
                    <CardContent><p className="text-2xl font-bold text-violet-500">{formatCurrency(totals.adminEarnings)}</p></CardContent></Card>
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Tips</CardTitle></CardHeader>
                    {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (tips), not a status signal (#2816) */}
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
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (driver earnings), not a status signal (#2816) */}
                                            <TableCell className="text-sm text-right tabular-nums text-emerald-500">{formatCurrency(r.driver_earnings || 0)}</TableCell>
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (platform revenue), not a status signal (#2816) */}
                                            <TableCell className="text-sm text-right tabular-nums text-violet-500">{formatCurrency(r.admin_earnings || 0)}</TableCell>
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (tips), not a status signal (#2816) */}
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

function SpinrPassRevenueTab() {
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");
    // chart-palette's series never grows a 6th generated hue (by design), so
    // the overflow bucket for a 6-area comparison stays a literal accent
    // colour rather than pulling from the shared ramp.
    const compareColors = [...c.series, "#ec4899"];
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
                            // eslint-disable-next-line no-restricted-syntax -- decorative brand accent for the "compare" toggle active state, not a status signal (#2816)
                            comparing ? "bg-violet-500 text-white border-violet-500" : "text-muted-foreground border-border hover:bg-muted"
                        }`}>
                        <GitCompareArrows className="h-3.5 w-3.5" /> Compare
                    </button>
                )}

                <div className="flex-1" />

                {/* Date + Export */}
                <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" aria-label="Start date" />
                <span className="text-muted-foreground text-sm">to</span>
                <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" aria-label="End date" />
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
                                    <span className="text-success font-semibold">{stats.active} active</span> · {stats.expired} expired · {stats.cancelled} cancelled
                                </p>
                            </CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><DollarSign className="h-4 w-4" /> Total Revenue</CardTitle></CardHeader>
                            {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816) */}
                            <CardContent><p className="text-2xl font-bold text-emerald-500">{formatCurrency(stats.total_revenue)}</p></CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><TrendingUp className="h-4 w-4" /> Active MRR</CardTitle></CardHeader>
                            {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816) */}
                            <CardContent><p className="text-2xl font-bold text-violet-500">{formatCurrency(stats.active_mrr)}</p></CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><CreditCard className="h-4 w-4" /> Period Revenue</CardTitle></CardHeader>
                            {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card accent color, not a status signal (#2816) */}
                            <CardContent><p className="text-2xl font-bold text-amber-500">{formatCurrency(stats.range_revenue)}</p>
                                <p className="text-xs text-muted-foreground mt-1">{stats.range_transactions} transactions</p>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Comparison Charts */}
                    {comparing && selectedAreas.length >= 2 ? (
                        compareLoading ? (
                            <div className="flex items-center justify-center py-12">
                                {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent on the comparison-loading spinner, not a status signal (#2816) */}
                                <div className="h-8 w-8 animate-spin rounded-full border-2 border-violet-500 border-t-transparent" />
                                <span className="ml-3 text-sm text-muted-foreground">Loading comparison...</span>
                            </div>
                        ) : (
                            <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent border for the comparison-mode card, not a status signal (#2816) */}
                                <Card className="border-violet-200 dark:border-violet-800">
                                    <CardHeader className="pb-2">
                                        <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent icon color, not a status signal (#2816) */}
                                            <GitCompareArrows className="h-4 w-4 text-violet-500" /> Revenue Comparison
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <ResponsiveContainer width="100%" height={220}>
                                            <LineChart data={buildCompareChart("daily_revenue", "amount")}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} tickFormatter={v => `$${v}`} />
                                                <Tooltip contentStyle={c.tooltip} labelStyle={{ fontWeight: 600 }} formatter={(v: any) => formatCurrency(Number(v || 0))} />
                                                <Legend />
                                                {selectedAreas.map((areaId, i) => {
                                                    const name = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                                                    return <Line key={areaId} type="monotone" dataKey={name} stroke={compareColors[i % compareColors.length]} strokeWidth={2} dot={{ r: 2 }} />;
                                                })}
                                            </LineChart>
                                        </ResponsiveContainer>
                                    </CardContent>
                                </Card>
                                {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent border for the comparison-mode card, not a status signal (#2816) */}
                                <Card className="border-violet-200 dark:border-violet-800">
                                    <CardHeader className="pb-2">
                                        <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent icon color, not a status signal (#2816) */}
                                            <GitCompareArrows className="h-4 w-4 text-violet-500" /> Subscribers Comparison
                                        </CardTitle>
                                    </CardHeader>
                                    <CardContent>
                                        <ResponsiveContainer width="100%" height={220}>
                                            <BarChart data={buildCompareChart("daily_subscribers", "count")}>
                                                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                                                <XAxis dataKey="date" tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} axisLine={false} tickLine={false} />
                                                <YAxis tick={{ fontSize: 10, fill: "hsl(var(--muted-foreground))" }} allowDecimals={false} axisLine={false} tickLine={false} />
                                                <Tooltip contentStyle={c.tooltip} labelStyle={{ fontWeight: 600 }} />
                                                <Legend />
                                                {selectedAreas.map((areaId, i) => {
                                                    const name = serviceAreas.find(a => a.id === areaId)?.name || areaId.slice(0, 8);
                                                    return <Bar key={areaId} dataKey={name} fill={compareColors[i % compareColors.length]} radius={[3, 3, 0, 0]} />;
                                                })}
                                            </BarChart>
                                        </ResponsiveContainer>
                                    </CardContent>
                                </Card>

                                {/* Comparison summary table */}
                                {/* eslint-disable-next-line no-restricted-syntax -- decorative brand accent border for the comparison-mode card, not a status signal (#2816) */}
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
                                                                <span className="inline-block w-3 h-3 rounded-full mr-2" style={{ backgroundColor: compareColors[i % compareColors.length] }} />
                                                                {name}
                                                            </TableCell>
                                                            <TableCell className="text-right">{s?.total_subscribers || 0}</TableCell>
                                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (active subscribers), not a status signal (#2816) */}
                                                            <TableCell className="text-right text-emerald-500 font-medium">{s?.active || 0}</TableCell>
                                                            <TableCell className="text-right font-semibold">{formatCurrency(s?.total_revenue || 0)}</TableCell>
                                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (MRR), not a status signal (#2816) */}
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
                                        {/* eslint-disable-next-line no-restricted-syntax -- decorative chart-card icon accent, not a status signal (#2816) */}
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
                                                <Tooltip contentStyle={c.tooltip} labelStyle={{ fontWeight: 600 }} formatter={(v: any) => [formatCurrency(Number(v || 0)), "Revenue"]} />
                                                <Line type="monotone" dataKey="amount" stroke={c.good} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
                                            </LineChart>
                                        </ResponsiveContainer>
                                    ) : <p className="text-sm text-muted-foreground py-8 text-center">No revenue data in this range</p>}
                                </CardContent>
                            </Card>
                            <Card className="border-border/50">
                                <CardHeader className="pb-2">
                                    <CardTitle className="text-sm font-semibold flex items-center gap-2">
                                        {/* eslint-disable-next-line no-restricted-syntax -- decorative chart-card icon accent, not a status signal (#2816) */}
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
                                                <Tooltip contentStyle={c.tooltip} labelStyle={{ fontWeight: 600 }} />
                                                <Bar dataKey="count" name="Subscribers" fill={c.accent} radius={[4, 4, 0, 0]} />
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
                                                {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (active subscribers), not a status signal (#2816) */}
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
                                            {/* eslint-disable-next-line no-restricted-syntax -- decorative category accent (transaction amount), not a status signal (#2816) */}
                                            <TableCell className="font-semibold text-emerald-500">{formatCurrency(t.price)}</TableCell>
                                            <TableCell>
                                                <Badge variant="secondary" className={
                                                    t.status === "active" ? "bg-success/15 text-success" :
                                                    t.status === "expired" ? "bg-warning/15 text-warning" :
                                                    "bg-destructive/15 text-destructive"
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
        if (s === "completed") return "bg-success/15 text-success";
        if (s === "pending") return "bg-warning/15 text-warning";
        if (s === "failed") return "bg-destructive/15 text-destructive";
        return "bg-muted text-muted-foreground";
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
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><CheckCircle className="h-4 w-4 text-success" /> Total Paid</div>
                        <div className="text-2xl font-bold text-success">${stats.total_paid?.toLocaleString()}</div>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><Clock className="h-4 w-4 text-warning" /> Pending</div>
                        <div className="text-2xl font-bold text-warning">${stats.total_pending?.toLocaleString()}</div>
                        <p className="text-xs text-muted-foreground">{stats.pending_count} payouts</p>
                    </CardContent></Card>
                    <Card><CardContent className="pt-4">
                        <div className="flex items-center gap-2 text-sm text-muted-foreground"><AlertTriangle className="h-4 w-4 text-destructive" /> Failed</div>
                        <div className="text-2xl font-bold text-destructive">${stats.total_failed?.toLocaleString()}</div>
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
                                                <p className="text-[10px] text-destructive mt-0.5 truncate max-w-[200px]" title={p.error_message}>
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
