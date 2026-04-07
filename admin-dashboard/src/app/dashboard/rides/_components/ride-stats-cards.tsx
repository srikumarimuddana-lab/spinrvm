"use client";

import { useEffect, useState } from "react";
import { getRideStats } from "@/lib/api";
import { CalendarCheck, CalendarMinus, CalendarRange, Calendar } from "lucide-react";

export default function RideStatsCards() {
    const [stats, setStats] = useState<any>(null);

    useEffect(() => {
        getRideStats().then(setStats).catch(() => {});
    }, []);

    if (!stats) return null;

    const cards = [
        { label: "Today", count: stats.today_count, sub: "", icon: CalendarCheck, color: "text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400" },
        { label: "Yesterday", count: stats.yesterday_count, sub: "", icon: CalendarMinus, color: "text-amber-600 bg-amber-100 dark:bg-amber-900/30 dark:text-amber-400" },
        { label: "This Week", count: stats.this_week_count, sub: `${stats.week_start} – ${stats.week_end}`, icon: CalendarRange, color: "text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400" },
        { label: "This Month", count: stats.this_month_count, sub: `${stats.month_start} – ${stats.month_end}`, icon: Calendar, color: "text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400" },
    ];

    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3 mb-4">
            {cards.map(c => (
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
    );
}
