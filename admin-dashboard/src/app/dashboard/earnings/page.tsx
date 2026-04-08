"use client";

import { useEffect, useState } from "react";
import { getEarnings, getSubscriptionStats } from "@/lib/api";
import { exportToCsv } from "@/lib/export-csv";
import { formatCurrency, formatDate, statusColor } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
    Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import { Download, Car, CreditCard, Users, TrendingUp, DollarSign, UserPlus, XCircle, Clock } from "lucide-react";
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
    const [tab, setTab] = useState<"rides" | "spinr-pass">("rides");

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-3xl font-bold tracking-tight">Earnings</h1>
                    <p className="text-muted-foreground mt-1">Platform revenue from rides and Spinr Pass subscriptions</p>
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
                    <CreditCard className="h-4 w-4" /> Spinr Pass Revenue
                </button>
            </div>

            {tab === "rides" && <RideEarningsTab />}
            {tab === "spinr-pass" && <SpinrPassRevenueTab />}
        </div>
    );
}

// ─── Ride Earnings Tab (existing) ───

function RideEarningsTab() {
    const [earnings, setEarnings] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    useEffect(() => {
        getEarnings()
            .then((data) => {
                if (Array.isArray(data)) { setEarnings(data); }
                else if (data && typeof data === "object") {
                    const arr = (data as any).earnings || (data as any).rides || (data as any).data;
                    setEarnings(Array.isArray(arr) ? arr : []);
                } else { setEarnings([]); }
            })
            .catch(() => {})
            .finally(() => setLoading(false));
    }, []);

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

            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                <Card className="border-border/50"><CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground">Total Fares</CardTitle></CardHeader>
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

function SpinrPassRevenueTab() {
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [dateFrom, setDateFrom] = useState("");
    const [dateTo, setDateTo] = useState("");

    const loadData = () => {
        setLoading(true);
        const params: any = {};
        if (dateFrom) params.start_date = dateFrom;
        if (dateTo) params.end_date = dateTo;
        getSubscriptionStats(params)
            .then(setData)
            .catch(() => {})
            .finally(() => setLoading(false));
    };

    useEffect(() => { loadData(); }, [dateFrom, dateTo]);

    const stats = data?.stats;
    const transactions = data?.transactions || [];
    const planBreakdown = data?.plan_breakdown || [];
    const revenueChart = data?.charts?.daily_revenue || [];
    const subsChart = data?.charts?.daily_subscribers || [];

    return (
        <div className="space-y-6">
            {/* Date filter + Export */}
            <div className="flex items-center justify-end gap-2">
                <Input type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} className="w-36 text-xs" />
                <span className="text-muted-foreground text-sm">to</span>
                <Input type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} className="w-36 text-xs" />
                <Button variant="outline" onClick={() => exportToCsv("spinr-pass-transactions", transactions, [
                    { key: "driver_name", label: "Driver" }, { key: "plan_name", label: "Plan" },
                    { key: "price", label: "Amount" }, { key: "status", label: "Status" },
                    { key: "started_at", label: "Started" }, { key: "expires_at", label: "Expires" },
                    { key: "created_at", label: "Transaction Date" },
                ])} disabled={transactions.length === 0}>
                    <Download className="mr-2 h-4 w-4" /> Export CSV
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
                            <CardContent><p className="text-2xl font-bold text-emerald-500">{formatCurrency(stats.total_revenue)}</p>
                                <p className="text-xs text-muted-foreground mt-1">All time subscription income</p>
                            </CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><TrendingUp className="h-4 w-4" /> Active MRR</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold text-violet-500">{formatCurrency(stats.active_mrr)}</p>
                                <p className="text-xs text-muted-foreground mt-1">From {stats.active} active subscriptions</p>
                            </CardContent>
                        </Card>
                        <Card className="border-border/50">
                            <CardHeader className="pb-2"><CardTitle className="text-sm text-muted-foreground flex items-center gap-1.5"><CreditCard className="h-4 w-4" /> Period Revenue</CardTitle></CardHeader>
                            <CardContent><p className="text-2xl font-bold text-amber-500">{formatCurrency(stats.range_revenue)}</p>
                                <p className="text-xs text-muted-foreground mt-1">{stats.range_transactions} transactions in range</p>
                            </CardContent>
                        </Card>
                    </div>

                    {/* Charts */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        {/* Revenue Chart */}
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

                        {/* New Subscribers Chart */}
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

                    {/* Plan Breakdown */}
                    {planBreakdown.length > 0 && (
                        <Card className="border-border/50">
                            <CardHeader className="pb-2">
                                <CardTitle className="text-sm font-semibold">Plan Performance</CardTitle>
                            </CardHeader>
                            <CardContent className="p-0">
                                <Table>
                                    <TableHeader>
                                        <TableRow>
                                            <TableHead>Plan</TableHead>
                                            <TableHead className="text-right">Subscribers</TableHead>
                                            <TableHead className="text-right">Active</TableHead>
                                            <TableHead className="text-right">Revenue</TableHead>
                                        </TableRow>
                                    </TableHeader>
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
                                <TableHeader>
                                    <TableRow>
                                        <TableHead>Driver</TableHead>
                                        <TableHead>Plan</TableHead>
                                        <TableHead>Amount</TableHead>
                                        <TableHead>Status</TableHead>
                                        <TableHead>Started</TableHead>
                                        <TableHead>Expires</TableHead>
                                    </TableRow>
                                </TableHeader>
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
