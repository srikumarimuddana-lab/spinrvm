"use client";

import { formatCurrency } from "@/lib/utils";
import {
    Users, Wifi, ShieldCheck, ShieldAlert, Car, DollarSign, Star,
} from "lucide-react";

interface DriverStatsData {
    total: number;
    online: number;
    verified: number;
    unverified: number;
    total_rides: number;
    total_earnings: number;
    avg_rating: number;
}

function StatCard({ icon: I, color, bg, label, value }: {
    icon: any; color: string; bg: string; label: string; value: string | number;
}) {
    return (
        <div className="bg-card border rounded-xl p-4 flex items-center gap-3 hover:shadow-sm transition-shadow">
            <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${bg}`}>
                <I className={`h-5 w-5 ${color}`} />
            </div>
            <div className="min-w-0">
                <p className="text-xl font-extrabold leading-tight">{typeof value === "number" ? value.toLocaleString() : value}</p>
                <p className="text-[11px] text-muted-foreground font-medium mt-0.5">{label}</p>
            </div>
        </div>
    );
}

export default function DriverStatsCards({ stats, loading }: { stats: DriverStatsData | null; loading: boolean }) {
    if (loading || !stats) {
        return (
            <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
                {Array.from({ length: 7 }).map((_, i) => (
                    <div key={i} className="bg-card border rounded-xl p-4 h-[72px] animate-pulse">
                        <div className="flex items-center gap-3">
                            <div className="w-10 h-10 rounded-xl bg-muted" />
                            <div className="space-y-2 flex-1">
                                <div className="h-4 w-10 rounded bg-muted" />
                                <div className="h-3 w-14 rounded bg-muted" />
                            </div>
                        </div>
                    </div>
                ))}
            </div>
        );
    }

    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 xl:grid-cols-7 gap-3">
            <StatCard icon={Users}
                color="text-blue-600 dark:text-blue-400"
                bg="bg-blue-100 dark:bg-blue-900/30"
                label="Total Drivers" value={stats.total} />
            <StatCard icon={Wifi}
                color="text-emerald-600 dark:text-emerald-400"
                bg="bg-emerald-100 dark:bg-emerald-900/30"
                label="Online" value={stats.online} />
            <StatCard icon={ShieldCheck}
                color="text-green-600 dark:text-green-400"
                bg="bg-green-100 dark:bg-green-900/30"
                label="Verified" value={stats.verified} />
            <StatCard icon={ShieldAlert}
                color="text-amber-600 dark:text-amber-400"
                bg="bg-amber-100 dark:bg-amber-900/30"
                label="Unverified" value={stats.unverified} />
            <StatCard icon={Car}
                color="text-violet-600 dark:text-violet-400"
                bg="bg-violet-100 dark:bg-violet-900/30"
                label="Total Rides" value={stats.total_rides} />
            <StatCard icon={DollarSign}
                color="text-teal-600 dark:text-teal-400"
                bg="bg-teal-100 dark:bg-teal-900/30"
                label="Total Earnings" value={formatCurrency(stats.total_earnings)} />
            <StatCard icon={Star}
                color="text-orange-600 dark:text-orange-400"
                bg="bg-orange-100 dark:bg-orange-900/30"
                label="Avg Rating" value={stats.avg_rating?.toFixed(1) || "0.0"} />
        </div>
    );
}
