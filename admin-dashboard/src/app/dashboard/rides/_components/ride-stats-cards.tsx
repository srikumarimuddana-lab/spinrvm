"use client";

import { useEffect, useState, useCallback } from "react";
import { getRideTrend, getRideFinancials, type RideFinancialsPeriod } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import {
    Car, CheckCircle, DollarSign, BarChart3,
    Banknote, Gift, Receipt, Ticket, Landmark, TrendingUp, TrendingDown, Coins,
} from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

const PERIODS: { value: RideFinancialsPeriod; label: string }[] = [
    { value: "today", label: "Today" },
    { value: "yesterday", label: "Yesterday" },
    { value: "week", label: "This Week" },
    { value: "month", label: "This Month" },
];

function StatCard({ icon: I, color, bg, label, value, sub, tooltip }: {
    icon: any; color: string; bg: string; label: string; value: string | number; sub?: string; tooltip: string;
}) {
    return (
        <div className="bg-card border rounded-xl p-4 flex items-center gap-3.5 relative group cursor-default hover:shadow-sm transition-shadow">
            <div className={`w-11 h-11 rounded-xl flex items-center justify-center shrink-0 ${bg}`}>
                <I className={`h-5 w-5 ${color}`} />
            </div>
            <div className="min-w-0">
                <p className="text-2xl font-extrabold leading-tight">{value}</p>
                <p className="text-xs text-muted-foreground font-medium mt-0.5">{label}</p>
                {sub && <p className="text-[10px] text-muted-foreground/70 mt-0.5">{sub}</p>}
            </div>
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-foreground text-background text-[11px] rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
                {tooltip}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-2 h-2 bg-foreground rotate-45 -mt-1" />
            </div>
        </div>
    );
}

function RevenueCard({ icon: I, color, bg, label, value, tooltip, sub }: {
    icon: any; color: string; bg: string; label: string; value: string; tooltip: string;
    sub?: { l: string; v: string }[];
}) {
    return (
        <div className="bg-card border rounded-xl p-3.5 flex items-center gap-3 relative group cursor-default hover:shadow-sm transition-shadow">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${bg}`}>
                <I className={`h-4 w-4 ${color}`} />
            </div>
            <div className="min-w-0 flex-1">
                <p className="text-lg font-bold leading-tight">{value}</p>
                <p className="text-[11px] text-muted-foreground font-medium">{label}</p>
                {sub && sub.length > 0 && (
                    <div className="mt-1.5 pt-1.5 border-t border-border/60 space-y-0.5">
                        {sub.map((s) => (
                            <div key={s.l} className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground tabular-nums">
                                <span>{s.l}</span><span className="font-semibold text-foreground/80">{s.v}</span>
                            </div>
                        ))}
                    </div>
                )}
            </div>
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-foreground text-background text-[11px] rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
                {tooltip}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-2 h-2 bg-foreground rotate-45 -mt-1" />
            </div>
        </div>
    );
}

export default function RideStatsCards() {
    const [period, setPeriod] = useState<RideFinancialsPeriod>("today");
    const [fin, setFin] = useState<any>(null);
    const [loading, setLoading] = useState(true);

    const load = useCallback(async (p: RideFinancialsPeriod) => {
        setLoading(true);
        try {
            setFin(await getRideFinancials(p));
        } catch { /* keep previous */ } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { load(period); }, [period, load]);

    const f = fin || {};
    const platformAfterNeg = (f.platform_after_promo ?? 0) < 0;

    return (
        <div className="space-y-3">
            {/* Period pills */}
            <div className="flex items-center justify-between flex-wrap gap-2">
                <div className="inline-flex rounded-lg border border-border bg-card p-0.5">
                    {PERIODS.map((opt) => (
                        <button key={opt.value} type="button" onClick={() => setPeriod(opt.value)}
                            className={`px-3 py-1.5 text-xs font-semibold rounded transition-colors ${
                                period === opt.value ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:text-foreground"
                            }`}>
                            {opt.label}
                        </button>
                    ))}
                </div>
                {f.label && <p className="text-xs text-muted-foreground">{f.label}{loading ? " · updating…" : ""}</p>}
            </div>

            {/* Operations */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <StatCard icon={Car} color="text-blue-600 dark:text-blue-400" bg="bg-blue-100 dark:bg-blue-900/30"
                    label="Rides" value={f.rides_count ?? 0}
                    tooltip={`${f.rides_count ?? 0} rides created in this period`} />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <StatCard icon={CheckCircle} color="text-emerald-600 dark:text-emerald-400" bg="bg-emerald-100 dark:bg-emerald-900/30"
                    label="Completed" value={f.completed_count ?? 0}
                    tooltip={`${f.completed_count ?? 0} rides completed in this period`} />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <StatCard icon={Banknote} color="text-emerald-600 dark:text-emerald-400" bg="bg-emerald-100 dark:bg-emerald-900/30"
                    label="Actual Sale (rider paid)" value={formatCurrency(f.rider_paid ?? 0)}
                    tooltip="What riders were actually charged (grand total, after promo)" />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <StatCard icon={DollarSign} color="text-teal-600 dark:text-teal-400" bg="bg-teal-100 dark:bg-teal-900/30"
                    label="Gross Fare" value={formatCurrency(f.gross_fare ?? 0)}
                    tooltip="Ride fare subtotal before area fees, tax and promo" />
            </div>

            {/* Where the money goes */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Car} color="text-emerald-600 dark:text-emerald-400" bg="bg-emerald-100 dark:bg-emerald-900/30"
                    label="Driver Revenue (all-in)" value={formatCurrency(f.driver_take ?? 0)}
                    tooltip="Driver all-in take = ride fare + tips + incentives"
                    sub={[
                        { l: "Ride fare", v: formatCurrency(f.driver_revenue ?? 0) },
                        { l: "Tips", v: formatCurrency(f.tips ?? 0) },
                        { l: "Incentives", v: formatCurrency(f.incentives ?? 0) },
                    ]} />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={TrendingUp} color="text-amber-600 dark:text-amber-400" bg="bg-amber-100 dark:bg-amber-900/30"
                    label="Tips" value={formatCurrency(f.tips ?? 0)}
                    tooltip="Tips collected — 100% to drivers" />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Gift} color="text-pink-600 dark:text-pink-400" bg="bg-pink-100 dark:bg-pink-900/30"
                    label="Incentives" value={formatCurrency(f.incentives ?? 0)}
                    tooltip="Platform-funded driver bonuses paid in this period" />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Receipt} color="text-slate-600 dark:text-slate-400" bg="bg-slate-100 dark:bg-slate-800/50"
                    label="GST Collected" value={formatCurrency(f.gst_collected ?? 0)}
                    tooltip="Tax collected from riders — pass-through, remitted by drivers" />
            </div>

            {/* Platform books */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Ticket} color="text-violet-600 dark:text-violet-400" bg="bg-violet-100 dark:bg-violet-900/30"
                    label="Promo Applied" value={formatCurrency(f.promo_applied ?? 0)}
                    tooltip="Total promo discount absorbed by the platform" />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Coins} color="text-indigo-600 dark:text-indigo-400" bg="bg-indigo-100 dark:bg-indigo-900/30"
                    label="Area Fees" value={formatCurrency(f.area_fees ?? 0)}
                    tooltip="Platform / Insurance / City / Infrastructure fees collected" />
                {/* eslint-disable-next-line no-restricted-syntax -- decorative KPI-card icon tint, not a status signal (#2816) */}
                <RevenueCard icon={Landmark} color="text-blue-600 dark:text-blue-400" bg="bg-blue-100 dark:bg-blue-900/30"
                    label="Platform Sales (pre-promo)" value={formatCurrency(f.platform_before_promo ?? 0)}
                    tooltip="Booking + airport + area fees, before incentives/promo" />
                <RevenueCard icon={platformAfterNeg ? TrendingDown : TrendingUp}
                    color={platformAfterNeg ? "text-destructive" : "text-success"}
                    bg={platformAfterNeg ? "bg-destructive/15" : "bg-success/15"}
                    label="Platform Net (post-promo)" value={formatCurrency(f.platform_after_promo ?? 0)}
                    tooltip="Platform sales after subtracting incentives funded and promo absorbed"
                    sub={[
                        { l: "Pre-promo sales", v: formatCurrency(f.platform_before_promo ?? 0) },
                        { l: "Less incentives", v: `-${formatCurrency(f.incentives ?? 0)}` },
                        { l: "Less promo", v: `-${formatCurrency(f.promo_applied ?? 0)}` },
                    ]} />
            </div>
        </div>
    );
}

export function RidesChart() {
    const [stats, setStats] = useState<any>(null);

    useEffect(() => {
        getRideTrend(14).then(setStats).catch(() => {});
    }, []);

    if (!stats?.daily_chart?.length) return null;

    return (
        <div className="bg-card border rounded-xl p-5">
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-2.5">
                    <div className="w-9 h-9 rounded-lg bg-primary/10 flex items-center justify-center">
                        <BarChart3 className="h-4.5 w-4.5 text-primary" />
                    </div>
                    <div>
                        <h3 className="text-sm font-semibold">Ride Trends</h3>
                        <p className="text-xs text-muted-foreground">Daily ride volume over the last 14 days</p>
                    </div>
                </div>
            </div>
            <ResponsiveContainer width="100%" height={220}>
                <BarChart data={stats.daily_chart} barSize={24}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" vertical={false} />
                    <XAxis
                        dataKey="date"
                        tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                        axisLine={false}
                        tickLine={false}
                    />
                    <YAxis
                        tick={{ fontSize: 11, fill: "hsl(var(--muted-foreground))" }}
                        allowDecimals={false}
                        axisLine={false}
                        tickLine={false}
                    />
                    <Tooltip
                        contentStyle={{
                            fontSize: 12,
                            borderRadius: 10,
                            border: '1px solid hsl(var(--border))',
                            background: 'hsl(var(--card))',
                            boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
                        }}
                        labelStyle={{ fontWeight: 600 }}
                        cursor={{ fill: 'hsl(var(--muted))', radius: 4 }}
                    />
                    <Bar dataKey="rides" fill="hsl(var(--primary))" radius={[6, 6, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}
