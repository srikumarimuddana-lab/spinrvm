"use client";

import { useEffect, useState } from "react";
import {
    Download, DollarSign, UserPlus, MapPin, X, GitCompareArrows, TrendingUp, CreditCard, Users,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { useTableSort, SortableHead } from "@/components/ui/sortable-table";
import { Input } from "@/components/ui/input";
import {
    BarChart, Bar, LineChart, Line, XAxis, YAxis, Tooltip, Legend,
    ResponsiveContainer, CartesianGrid,
} from "recharts";
import { useTheme } from "next-themes";
import { chartColors } from "@/components/analytics/chart-palette";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate } from "@/lib/utils";
import { getSubscriptionStats } from "@/lib/api";

export function SpinrPassRevenueTab() {
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
                                                    "bg-destructive/15 text-destructive dark:text-[#ff453a]"
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
