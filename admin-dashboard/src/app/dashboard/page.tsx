"use client";

import { useEffect, useState } from "react";
import { getStats } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
    Car,
    Users,
    DollarSign,
    TrendingUp,
    Activity,
    UserCheck,
    XCircle,
    Zap,
} from "lucide-react";

interface Stats {
    total_rides: number;
    completed_rides: number;
    cancelled_rides: number;
    active_rides: number;
    total_drivers: number;
    online_drivers: number;
    total_users: number;
    total_driver_earnings: number;
    total_admin_earnings: number;
    total_tips: number;
}

const CARD_CONFIGS = [
    {
        key: "total_rides",
        label: "Total Rides",
        icon: Car,
        color: "from-blue-600 to-blue-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "active_rides",
        label: "Active Rides",
        icon: Activity,
        color: "from-emerald-600 to-emerald-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "completed_rides",
        label: "Completed",
        icon: Zap,
        color: "from-violet-600 to-violet-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "cancelled_rides",
        label: "Cancelled",
        icon: XCircle,
        color: "from-red-600 to-red-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "total_users",
        label: "Total Users",
        icon: Users,
        color: "from-sky-600 to-sky-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "total_drivers",
        label: "Total Drivers",
        icon: UserCheck,
        color: "from-amber-600 to-amber-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "online_drivers",
        label: "Online Drivers",
        icon: TrendingUp,
        color: "from-teal-600 to-teal-400",
        format: (v: number) => v.toLocaleString(),
    },
    {
        key: "total_admin_earnings",
        label: "Platform Revenue",
        icon: DollarSign,
        color: "from-pink-600 to-pink-400",
        format: (v: number) => formatCurrency(v),
    },
] as const;

export default function DashboardPage() {
    const [stats, setStats] = useState<Stats | null>(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        getStats()
            .then(setStats)
            .catch(() => { })
            .finally(() => setLoading(false));
    }, []);

    return (
        <div className="space-y-8">
            <div>
                <h1 className="text-3xl font-bold tracking-tight">Dashboard</h1>
                <p className="text-muted-foreground mt-1">
                    Overview of the Spinr rideshare platform.
                </p>
            </div>

            {loading ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    {Array.from({ length: 8 }).map((_, i) => (
                        <Card key={i} className="animate-pulse">
                            <CardHeader className="pb-2">
                                <div className="h-4 w-24 rounded bg-muted" />
                            </CardHeader>
                            <CardContent>
                                <div className="h-8 w-16 rounded bg-muted" />
                            </CardContent>
                        </Card>
                    ))}
                </div>
            ) : stats ? (
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    {CARD_CONFIGS.map((cfg) => {
                        const Icon = cfg.icon;
                        const value = stats[cfg.key as keyof Stats] as number;
                        return (
                            <Card
                                key={cfg.key}
                                className="group relative overflow-hidden border-border/50 transition-all hover:border-border hover:shadow-lg"
                            >
                                <div
                                    className={`absolute inset-0 bg-gradient-to-br ${cfg.color} opacity-[0.03] group-hover:opacity-[0.06] transition-opacity`}
                                />
                                <CardHeader className="flex flex-row items-center justify-between pb-2">
                                    <CardTitle className="text-sm font-medium text-muted-foreground">
                                        {cfg.label}
                                    </CardTitle>
                                    <div
                                        className={`rounded-lg bg-gradient-to-br ${cfg.color} p-2`}
                                    >
                                        <Icon className="h-4 w-4 text-white" />
                                    </div>
                                </CardHeader>
                                <CardContent>
                                    <div className="text-2xl font-bold">{cfg.format(value)}</div>
                                </CardContent>
                            </Card>
                        );
                    })}
                </div>
            ) : (
                <p className="text-muted-foreground">Failed to load stats.</p>
            )}

            {/* Quick summary cards for earnings */}
            {stats && (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                    <Card className="border-border/50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-muted-foreground">
                                Driver Earnings
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-emerald-500">
                                {formatCurrency(stats.total_driver_earnings)}
                            </div>
                        </CardContent>
                    </Card>
                    <Card className="border-border/50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-muted-foreground">
                                Platform Revenue
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-violet-500">
                                {formatCurrency(stats.total_admin_earnings)}
                            </div>
                        </CardContent>
                    </Card>
                    <Card className="border-border/50">
                        <CardHeader className="pb-2">
                            <CardTitle className="text-sm font-medium text-muted-foreground">
                                Total Tips
                            </CardTitle>
                        </CardHeader>
                        <CardContent>
                            <div className="text-2xl font-bold text-amber-500">
                                {formatCurrency(stats.total_tips)}
                            </div>
                        </CardContent>
                    </Card>
                </div>
            )}
        </div>
    );
}
