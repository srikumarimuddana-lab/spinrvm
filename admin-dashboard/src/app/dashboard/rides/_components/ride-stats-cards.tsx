"use client";

import { useEffect, useState } from "react";
import { getRideStats } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import {
    CalendarCheck, CalendarMinus, CalendarRange, Calendar,
    DollarSign, TrendingUp, CheckCircle, BarChart3,
} from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

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

function RevenueCard({ icon: I, color, bg, label, value, tooltip }: {
    icon: any; color: string; bg: string; label: string; value: string; tooltip: string;
}) {
    return (
        <div className="bg-card border rounded-xl p-3.5 flex items-center gap-3 relative group cursor-default hover:shadow-sm transition-shadow">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${bg}`}>
                <I className={`h-4 w-4 ${color}`} />
            </div>
            <div className="min-w-0">
                <p className="text-lg font-bold leading-tight">{value}</p>
                <p className="text-[11px] text-muted-foreground font-medium">{label}</p>
            </div>
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-foreground text-background text-[11px] rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
                {tooltip}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-2 h-2 bg-foreground rotate-45 -mt-1" />
            </div>
        </div>
    );
}

export default function RideStatsCards() {
    const [stats, setStats] = useState<any>(null);

    useEffect(() => {
        getRideStats().then(setStats).catch(() => {});
    }, []);

    if (!stats) return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="bg-card border rounded-xl p-4 h-[76px] animate-pulse">
                    <div className="flex items-center gap-3.5">
                        <div className="w-11 h-11 rounded-xl bg-muted" />
                        <div className="space-y-2 flex-1">
                            <div className="h-5 w-12 rounded bg-muted" />
                            <div className="h-3 w-16 rounded bg-muted" />
                        </div>
                    </div>
                </div>
            ))}
        </div>
    );

    return (
        <div className="space-y-3">
            {/* Ride Count Stats */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <StatCard icon={CalendarCheck}
                    color="text-blue-600 dark:text-blue-400"
                    bg="bg-blue-100 dark:bg-blue-900/30"
                    label="Today's Rides" value={stats.today_count}
                    tooltip={`${stats.today_count} ride${stats.today_count !== 1 ? "s" : ""} created today so far`} />
                <StatCard icon={CalendarMinus}
                    color="text-amber-600 dark:text-amber-400"
                    bg="bg-amber-100 dark:bg-amber-900/30"
                    label="Yesterday" value={stats.yesterday_count}
                    tooltip={`${stats.yesterday_count} ride${stats.yesterday_count !== 1 ? "s" : ""} created yesterday`} />
                <StatCard icon={CalendarRange}
                    color="text-emerald-600 dark:text-emerald-400"
                    bg="bg-emerald-100 dark:bg-emerald-900/30"
                    label="This Week" value={stats.this_week_count}
                    sub={`${stats.week_start} – ${stats.week_end}`}
                    tooltip={`${stats.this_week_count} rides from ${stats.week_start} to ${stats.week_end} (Mon–Sun)`} />
                <StatCard icon={Calendar}
                    color="text-violet-600 dark:text-violet-400"
                    bg="bg-violet-100 dark:bg-violet-900/30"
                    label="This Month" value={stats.this_month_count}
                    sub={`${stats.month_start} – ${stats.month_end}`}
                    tooltip={`${stats.this_month_count} rides from ${stats.month_start} to ${stats.month_end}`} />
            </div>

            {/* Revenue & Performance Stats */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <RevenueCard icon={DollarSign}
                    color="text-emerald-600 dark:text-emerald-400"
                    bg="bg-emerald-100 dark:bg-emerald-900/30"
                    label="Today Revenue"
                    value={formatCurrency(stats.today_revenue || 0)}
                    tooltip={`Total fare from ${stats.today_completed} completed ride${stats.today_completed !== 1 ? "s" : ""} today`} />
                <RevenueCard icon={TrendingUp}
                    color="text-amber-600 dark:text-amber-400"
                    bg="bg-amber-100 dark:bg-amber-900/30"
                    label="Today Tips"
                    value={formatCurrency(stats.today_tips || 0)}
                    tooltip="Total tips collected from completed rides today" />
                <RevenueCard icon={CheckCircle}
                    color="text-blue-600 dark:text-blue-400"
                    bg="bg-blue-100 dark:bg-blue-900/30"
                    label="Completed Today"
                    value={String(stats.today_completed || 0)}
                    tooltip={`${stats.today_completed} ride${stats.today_completed !== 1 ? "s" : ""} successfully completed today`} />
                <RevenueCard icon={DollarSign}
                    color="text-violet-600 dark:text-violet-400"
                    bg="bg-violet-100 dark:bg-violet-900/30"
                    label="Month Revenue"
                    value={formatCurrency(stats.month_revenue || 0)}
                    tooltip={`Revenue from all completed rides this month (${stats.month_start} – ${stats.month_end})`} />
            </div>
        </div>
    );
}

export function RidesChart() {
    const [stats, setStats] = useState<any>(null);

    useEffect(() => {
        getRideStats().then(setStats).catch(() => {});
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
