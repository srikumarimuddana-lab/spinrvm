"use client";

import { useEffect, useState } from "react";
import { getRideStats } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { CalendarCheck, CalendarMinus, CalendarRange, Calendar, DollarSign, TrendingUp, CheckCircle } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid } from "recharts";

export default function RideStatsCards() {
    const [stats, setStats] = useState<any>(null);

    useEffect(() => {
        getRideStats().then(setStats).catch(() => {});
    }, []);

    if (!stats) return null;

    const rideCards = [
        { label: "Today", count: stats.today_count, sub: "", icon: CalendarCheck, color: "text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400" },
        { label: "Yesterday", count: stats.yesterday_count, sub: "", icon: CalendarMinus, color: "text-amber-600 bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400" },
        { label: "This Week", count: stats.this_week_count, sub: `${stats.week_start} – ${stats.week_end}`, icon: CalendarRange, color: "text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400" },
        { label: "This Month", count: stats.this_month_count, sub: `${stats.month_start} – ${stats.month_end}`, icon: Calendar, color: "text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400" },
    ];

    const revenueCards = [
        { label: "Today Revenue", value: formatCurrency(stats.today_revenue || 0), icon: DollarSign, color: "text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400" },
        { label: "Today Tips", value: formatCurrency(stats.today_tips || 0), icon: TrendingUp, color: "text-amber-600 bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400" },
        { label: "Completed Today", value: String(stats.today_completed || 0), icon: CheckCircle, color: "text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400" },
        { label: "Month Revenue", value: formatCurrency(stats.month_revenue || 0), icon: DollarSign, color: "text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400" },
    ];

    return (
        <div className="space-y-4 mb-4">
            {/* Ride count cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {rideCards.map(c => (
                    <div key={c.label} className="bg-card border rounded-xl p-4 flex items-center gap-3">
                        <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${c.color}`}>
                            <c.icon className="h-5 w-5" />
                        </div>
                        <div>
                            <p className="text-2xl font-extrabold">{c.count}</p>
                            <p className="text-xs text-muted-foreground font-medium">{c.label}</p>
                            {c.sub && <p className="text-[10px] text-muted-foreground">{c.sub}</p>}
                        </div>
                    </div>
                ))}
            </div>

            {/* Revenue cards */}
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                {revenueCards.map(c => (
                    <div key={c.label} className="bg-card border rounded-xl p-3 flex items-center gap-3">
                        <div className={`w-9 h-9 rounded-lg flex items-center justify-center shrink-0 ${c.color}`}>
                            <c.icon className="h-4 w-4" />
                        </div>
                        <div>
                            <p className="text-lg font-bold">{c.value}</p>
                            <p className="text-[10px] text-muted-foreground font-medium">{c.label}</p>
                        </div>
                    </div>
                ))}
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
