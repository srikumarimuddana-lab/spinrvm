"use client";

import { useEffect, useState } from "react";
import { getRideStats } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { CalendarCheck, CalendarMinus, CalendarRange, Calendar, DollarSign, TrendingUp, CheckCircle } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

function StatCard({ icon: I, color, label, value, sub, tooltip }: {
    icon: any; color: string; label: string; value: string | number; sub?: string; tooltip: string;
}) {
    return (
        <div className="bg-card border rounded-xl p-4 flex items-center gap-3 relative group cursor-default">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${color}`}>
                <I className="h-5 w-5" />
            </div>
            <div>
                <p className="text-2xl font-extrabold">{value}</p>
                <p className="text-xs text-muted-foreground font-medium">{label}</p>
                {sub && <p className="text-[10px] text-muted-foreground">{sub}</p>}
            </div>
            <div className="absolute bottom-full left-1/2 -translate-x-1/2 mb-2 px-3 py-1.5 bg-foreground text-background text-[11px] rounded-lg shadow-lg opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none whitespace-nowrap z-50">
                {tooltip}
                <div className="absolute top-full left-1/2 -translate-x-1/2 w-2 h-2 bg-foreground rotate-45 -mt-1" />
            </div>
        </div>
    );
}

function MiniStatCard({ icon: I, color, label, value, tooltip }: {
    icon: any; color: string; label: string; value: string; tooltip: string;
}) {
    return (
        <div className="bg-card border rounded-xl p-3 flex items-center gap-3 relative group cursor-default">
            <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${color}`}>
                <I className="h-4 w-4" />
            </div>
            <div>
                <p className="text-lg font-bold">{value}</p>
                <p className="text-[10px] text-muted-foreground font-medium">{label}</p>
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

    if (!stats) return null;

    return (
        <div className="space-y-3 mb-4">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <StatCard icon={CalendarCheck} color="text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400"
                    label="Today" value={stats.today_count}
                    tooltip={`${stats.today_count} ride${stats.today_count !== 1 ? "s" : ""} created today so far`} />
                <StatCard icon={CalendarMinus} color="text-amber-600 bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400"
                    label="Yesterday" value={stats.yesterday_count}
                    tooltip={`${stats.yesterday_count} ride${stats.yesterday_count !== 1 ? "s" : ""} created yesterday`} />
                <StatCard icon={CalendarRange} color="text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400"
                    label="This Week" value={stats.this_week_count} sub={`${stats.week_start} – ${stats.week_end}`}
                    tooltip={`${stats.this_week_count} rides from ${stats.week_start} to ${stats.week_end} (Mon–Sun)`} />
                <StatCard icon={Calendar} color="text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400"
                    label="This Month" value={stats.this_month_count} sub={`${stats.month_start} – ${stats.month_end}`}
                    tooltip={`${stats.this_month_count} rides from ${stats.month_start} to ${stats.month_end}`} />
            </div>
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                <MiniStatCard icon={DollarSign} color="text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400"
                    label="Today Revenue" value={formatCurrency(stats.today_revenue || 0)}
                    tooltip={`Total fare from ${stats.today_completed} completed ride${stats.today_completed !== 1 ? "s" : ""} today`} />
                <MiniStatCard icon={TrendingUp} color="text-amber-600 bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400"
                    label="Today Tips" value={formatCurrency(stats.today_tips || 0)}
                    tooltip="Total tips collected from completed rides today" />
                <MiniStatCard icon={CheckCircle} color="text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400"
                    label="Completed Today" value={String(stats.today_completed || 0)}
                    tooltip={`${stats.today_completed} ride${stats.today_completed !== 1 ? "s" : ""} successfully completed today`} />
                <MiniStatCard icon={DollarSign} color="text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400"
                    label="Month Revenue" value={formatCurrency(stats.month_revenue || 0)}
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
        <div className="bg-card border rounded-xl p-4 mt-4">
            <h3 className="text-sm font-semibold mb-3">Rides – Last 14 Days</h3>
            <ResponsiveContainer width="100%" height={200}>
                <BarChart data={stats.daily_chart}>
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--border))" />
                    <XAxis dataKey="date" tick={{ fontSize: 10 }} />
                    <YAxis tick={{ fontSize: 10 }} allowDecimals={false} />
                    <Tooltip
                        contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid hsl(var(--border))' }}
                        labelStyle={{ fontWeight: 600 }}
                    />
                    <Bar dataKey="rides" fill="hsl(var(--primary))" radius={[4, 4, 0, 0]} />
                </BarChart>
            </ResponsiveContainer>
        </div>
    );
}
