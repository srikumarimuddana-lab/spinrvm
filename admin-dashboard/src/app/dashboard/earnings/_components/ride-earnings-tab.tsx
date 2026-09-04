"use client";

import { useEffect, useState } from "react";
import { Download, X } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
    Table, TableBody, TableCell, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate } from "@/lib/utils";
import {
    getEarningsOverview, getEarningsRides, getServiceAreas,
    type EarningsOverview, type EarningsPeriod, type EarningsRide,
} from "@/lib/api";
import { CeoMetricsHeader } from "./ceo-metrics-header";

export function RideEarningsTab() {
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
