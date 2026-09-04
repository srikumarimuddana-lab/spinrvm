"use client";

import {
    Filter, Hourglass, CheckCircle, Clock, AlertTriangle, Activity, DollarSign,
    Wallet, XCircle, UserCheck, TrendingUp,
} from "lucide-react";
import {
    Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import {
    BarChart, Bar, XAxis, YAxis, Tooltip,
    ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useTheme } from "next-themes";
import { chartColors } from "@/components/analytics/chart-palette";
import type { EarningsPeriod, PayoutsOverview } from "@/lib/api";
import { PERIOD_OPTIONS, fmtMoney, fmtCount, fmtPct, fmtHours } from "./earnings-format";
import { MetricCard } from "./metric-card";

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
                <Card className={`border-border/50 ${stuck_over_48h.count > 0 ? "border-warning/40" : ""}`}>
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                                <Hourglass className="h-3.5 w-3.5" />
                                Stuck &gt; 48h
                            </div>
                            <span className="text-[10px] text-muted-foreground">manual review</span>
                        </div>
                        <p className={`text-2xl font-bold tabular-nums mt-1.5 ${stuck_over_48h.count > 0 ? "text-warning" : ""}`}>
                            {stuck_over_48h.count}
                        </p>
                        <p className="text-[11px] text-muted-foreground mt-1">{fmtMoney(stuck_over_48h.amount)} held up</p>
                    </CardContent>
                </Card>
                <Card className={`border-border/50 ${blocked_drivers.count > 0 ? "border-destructive/40" : ""}`}>
                    <CardContent className="p-4">
                        <div className="flex items-center justify-between">
                            <div className="flex items-center gap-2 text-[10px] uppercase tracking-wide text-muted-foreground font-semibold">
                                <XCircle className="h-3.5 w-3.5" />
                                Blocked by Stripe
                            </div>
                            <span className="text-[10px] text-muted-foreground">payouts_enabled=false</span>
                        </div>
                        <p className={`text-2xl font-bold tabular-nums mt-1.5 ${blocked_drivers.count > 0 ? "text-destructive" : ""}`}>
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
                            <AlertTriangle className="h-4 w-4 text-destructive" />
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
                            <UserCheck className="h-4 w-4 text-warning" />
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
                                            <TableCell className="text-xs text-right tabular-nums font-semibold text-destructive">
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
                        <TrendingUp className="h-4 w-4 text-success" />
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

export function PayoutsCeoHeader({
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
    const { resolvedTheme } = useTheme();
    const c = chartColors(resolvedTheme === "dark");
    return (
        <div className="space-y-4">
            <div className="flex items-end justify-between gap-3 flex-wrap">
                <div>
                    <h2 className="text-sm font-semibold uppercase tracking-wider text-muted-foreground">
                        Payout flow
                    </h2>
                    <p className="text-xs text-muted-foreground mt-0.5">
                        {overview?.period?.label ?? "Loading…"} · compared to prior {overview?.period?.days ?? "—"} days
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
                {/* Outstanding payable — the dominant working-capital
                    number for a 0% commission marketplace. Amber accent
                    because high outstanding is operational debt to drivers,
                    not a positive metric. Snapshot value (not period-windowed). */}
                <MetricCard
                    icon={Hourglass}
                    label="Outstanding to drivers"
                    metric={m?.outstanding_payable}
                    format={fmtMoney}
                    accent="text-warning"
                    loading={loading}
                />
                <MetricCard
                    icon={CheckCircle}
                    label="Paid out"
                    metric={m?.total_paid_out}
                    format={fmtMoney}
                    accent="text-success"
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
                    accent="text-destructive"
                    loading={loading}
                />
                <MetricCard
                    icon={Activity}
                    label="Success rate"
                    metric={m?.success_rate_pct}
                    format={fmtPct}
                    accent="text-success"
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
                                    contentStyle={c.tooltip}
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
