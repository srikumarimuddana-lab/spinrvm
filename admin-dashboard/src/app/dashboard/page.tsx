"use client";

import { useEffect, useState } from "react";
import { getStats, getSubscriptionPlans, getDriverSubscriptions } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { useAuthStore } from "@/store/authStore";
import {
    Car, Users, DollarSign, TrendingUp, Activity, UserCheck,
    XCircle, Zap, CreditCard, Ticket, Gift, ArrowUpRight,
    ArrowDownRight, Clock, MapPin,
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

export default function DashboardPage() {
    const { user } = useAuthStore();
    const [stats, setStats] = useState<Stats | null>(null);
    const [plans, setPlans] = useState<any[]>([]);
    const [subs, setSubs] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        Promise.all([
            getStats().catch(() => null),
            getSubscriptionPlans().catch(() => []),
            getDriverSubscriptions().catch(() => []),
        ]).then(([s, p, sub]) => {
            setStats(s); setPlans(p); setSubs(sub);
        }).finally(() => setLoading(false));
    }, []);

    const activeSubs = subs.filter(s => s.status === 'active');
    const subRevenue = activeSubs.reduce((sum, s) => sum + (s.price || 0), 0);

    const greeting = () => {
        const h = new Date().getHours();
        if (h < 12) return 'Good morning';
        if (h < 17) return 'Good afternoon';
        return 'Good evening';
    };

    if (loading) {
        return (
            <div className="space-y-6">
                <div className="h-8 w-64 rounded-lg bg-muted animate-pulse" />
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                    {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="h-28 rounded-2xl bg-muted animate-pulse" />
                    ))}
                </div>
            </div>
        );
    }

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-foreground">
                        {greeting()}, {user?.first_name || 'Admin'}
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        Here's what's happening with Spinr today.
                    </p>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded-full">
                    <Clock className="h-3 w-3" />
                    {new Date().toLocaleDateString('en-CA', { weekday: 'long', month: 'short', day: 'numeric' })}
                </div>
            </div>

            {/* Top Stats Row */}
            {stats && (
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                    <StatCard icon={Car} label="Total Rides" value={stats.total_rides} color="blue" />
                    <StatCard icon={Activity} label="Active Now" value={stats.active_rides} color="emerald" pulse />
                    <StatCard icon={Users} label="Total Users" value={stats.total_users} color="violet" />
                    <StatCard icon={UserCheck} label="Online Drivers" value={stats.online_drivers} subtitle={`of ${stats.total_drivers} total`} color="amber" />
                </div>
            )}

            {/* Revenue Row */}
            {stats && (
                <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                    <RevenueCard label="Platform Revenue" value={stats.total_admin_earnings} icon={DollarSign} color="from-red-500 to-rose-600" />
                    <RevenueCard label="Driver Earnings" value={stats.total_driver_earnings} icon={TrendingUp} color="from-emerald-500 to-teal-600" />
                    <RevenueCard label="Spinr Pass Revenue" value={subRevenue} icon={CreditCard} color="from-violet-500 to-purple-600" subtitle={`${activeSubs.length} active subscribers`} />
                </div>
            )}

            {/* Ride Stats + Spinr Pass */}
            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
                {/* Ride Breakdown */}
                {stats && (
                    <div className="bg-card border rounded-2xl p-5">
                        <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider mb-4">Ride Breakdown</h3>
                        <div className="space-y-3">
                            <BarStat label="Completed" value={stats.completed_rides} total={stats.total_rides} color="bg-emerald-500" />
                            <BarStat label="Cancelled" value={stats.cancelled_rides} total={stats.total_rides} color="bg-red-400" />
                            <BarStat label="Active" value={stats.active_rides} total={stats.total_rides} color="bg-blue-500" />
                        </div>
                        <div className="flex items-center gap-4 mt-4 pt-4 border-t">
                            <div className="flex-1">
                                <p className="text-xs text-muted-foreground">Tips Collected</p>
                                <p className="text-lg font-bold text-amber-500">{formatCurrency(stats.total_tips ?? 0)}</p>
                            </div>
                            <div className="flex-1">
                                <p className="text-xs text-muted-foreground">Completion Rate</p>
                                <p className="text-lg font-bold text-emerald-500">
                                    {stats.total_rides > 0 ? ((stats.completed_rides / stats.total_rides) * 100).toFixed(1) : 0}%
                                </p>
                            </div>
                        </div>
                    </div>
                )}

                {/* Spinr Pass Stats */}
                <div className="bg-card border rounded-2xl p-5">
                    <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider mb-4">Spinr Pass</h3>
                    {plans.length > 0 ? (
                        <div className="space-y-3">
                            {plans.map(plan => {
                                const planSubs = subs.filter(s => s.plan_id === plan.id && s.status === 'active');
                                return (
                                    <div key={plan.id} className="flex items-center gap-3 p-3 rounded-xl bg-muted/30">
                                        <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-red-500 to-rose-600 flex items-center justify-center shrink-0">
                                            <CreditCard className="h-5 w-5 text-white" />
                                        </div>
                                        <div className="flex-1 min-w-0">
                                            <p className="text-sm font-semibold truncate">{plan.name}</p>
                                            <p className="text-xs text-muted-foreground">
                                                ${plan.price?.toFixed(2)} · {plan.rides_per_day === -1 ? 'Unlimited' : `${plan.rides_per_day}/day`}
                                            </p>
                                        </div>
                                        <div className="text-right">
                                            <p className="text-sm font-bold">{planSubs.length}</p>
                                            <p className="text-[10px] text-muted-foreground">active</p>
                                        </div>
                                    </div>
                                );
                            })}
                            <div className="flex items-center justify-between pt-3 border-t">
                                <p className="text-xs text-muted-foreground">Monthly recurring</p>
                                <p className="text-lg font-bold text-violet-500">{formatCurrency(subRevenue)}</p>
                            </div>
                        </div>
                    ) : (
                        <div className="text-center py-8">
                            <CreditCard className="h-8 w-8 text-muted-foreground/30 mx-auto mb-2" />
                            <p className="text-sm text-muted-foreground">No plans created yet</p>
                        </div>
                    )}
                </div>
            </div>

            {/* Quick Stats Row */}
            {stats && (
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
                    <MiniCard icon={Zap} label="Completed" value={stats.completed_rides} color="text-emerald-500" />
                    <MiniCard icon={XCircle} label="Cancelled" value={stats.cancelled_rides} color="text-red-400" />
                    <MiniCard icon={Gift} label="Tips Given" value={formatCurrency(stats.total_tips ?? 0)} color="text-amber-500" />
                    <MiniCard icon={MapPin} label="Drivers Total" value={stats.total_drivers} color="text-blue-500" />
                </div>
            )}
        </div>
    );
}

// ─── Components ───

function StatCard({ icon: Icon, label, value, color, subtitle, pulse }: {
    icon: any; label: string; value: number; color: string; subtitle?: string; pulse?: boolean;
}) {
    return (
        <div className="bg-card border rounded-2xl p-4 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
                <div className={`w-8 h-8 rounded-lg bg-${color}-500/10 flex items-center justify-center`}>
                    <Icon className={`h-4 w-4 text-${color}-500`} />
                </div>
            </div>
            <div className="flex items-end gap-2">
                <p className="text-2xl font-bold tracking-tight">{(value ?? 0).toLocaleString()}</p>
                {pulse && value > 0 && <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse mb-2" />}
            </div>
            {subtitle && <p className="text-[11px] text-muted-foreground mt-1">{subtitle}</p>}
        </div>
    );
}

function RevenueCard({ label, value, icon: Icon, color, subtitle }: {
    label: string; value: number; icon: any; color: string; subtitle?: string;
}) {
    return (
        <div className={`bg-gradient-to-br ${color} rounded-2xl p-5 text-white`}>
            <div className="flex items-center gap-2 mb-3">
                <Icon className="h-5 w-5 text-white/80" />
                <p className="text-sm font-medium text-white/80">{label}</p>
            </div>
            <p className="text-3xl font-extrabold">{formatCurrency(value ?? 0)}</p>
            {subtitle && <p className="text-xs text-white/60 mt-1">{subtitle}</p>}
        </div>
    );
}

function BarStat({ label, value, total, color }: { label: string; value: number; total: number; color: string }) {
    const pct = total > 0 ? (value / total) * 100 : 0;
    return (
        <div>
            <div className="flex items-center justify-between mb-1">
                <p className="text-sm font-medium">{label}</p>
                <p className="text-sm font-bold">{(value ?? 0).toLocaleString()}</p>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
            </div>
        </div>
    );
}

function MiniCard({ icon: Icon, label, value, color }: { icon: any; label: string; value: any; color: string }) {
    return (
        <div className="bg-card border rounded-xl p-3 flex items-center gap-3">
            <Icon className={`h-5 w-5 ${color} shrink-0`} />
            <div className="min-w-0">
                <p className="text-xs text-muted-foreground">{label}</p>
                <p className="text-sm font-bold truncate">{typeof value === 'number' ? value.toLocaleString() : value}</p>
            </div>
        </div>
    );
}
