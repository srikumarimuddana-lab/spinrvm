"use client";

import { useEffect, useState } from "react";
import { getDashboardOverview, getServiceAreas } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { useAuthStore } from "@/store/authStore";
import {
    Car, Users, DollarSign, TrendingUp, Activity, UserCheck,
    CreditCard, Clock, MapPin, UserPlus, Wifi,
} from "lucide-react";

const RANGES = [
    { id: "today", label: "Today" },
    { id: "yesterday", label: "Yesterday" },
    { id: "24h", label: "Last 24h" },
    { id: "7d", label: "Last 7 days" },
];

export default function DashboardPage() {
    const { user } = useAuthStore();
    const [range, setRange] = useState("24h");
    const [areaId, setAreaId] = useState<string | null>(null);
    const [areas, setAreas] = useState<{ id: string; name?: string }[]>([]);
    const [data, setData] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(false);

    useEffect(() => {
        getServiceAreas().then((a) => setAreas(Array.isArray(a) ? a : [])).catch(() => {});
    }, []);

    // Re-fetch whenever the time window or service area changes.
    useEffect(() => {
        let cancelled = false;
        setLoading(true);
        setError(false);
        getDashboardOverview({ range, service_area_id: areaId })
            .then((d) => { if (!cancelled) setData(d); })
            .catch(() => { if (!cancelled) setError(true); })
            .finally(() => { if (!cancelled) setLoading(false); });
        return () => { cancelled = true; };
    }, [range, areaId]);

    const drivers = data?.drivers ?? {};
    const riders = data?.riders ?? {};
    const rides = data?.rides ?? {};
    const bd = rides.breakdown ?? {};
    const money = data?.money ?? {};
    const aggOff = money.aggregates_enabled === false;
    const rangeLabel = RANGES.find((r) => r.id === range)?.label ?? "";

    const greeting = () => {
        const h = new Date().getHours();
        if (h < 12) return "Good morning";
        if (h < 17) return "Good afternoon";
        return "Good evening";
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-foreground">
                        {greeting()}, {user?.first_name || "Admin"}
                    </h1>
                    <p className="text-sm text-muted-foreground mt-1">
                        {rangeLabel}{areaId ? ` · ${areas.find((a) => a.id === areaId)?.name ?? "Area"}` : " · All areas"}
                    </p>
                </div>
                <div className="flex items-center gap-2 text-xs text-muted-foreground bg-muted/50 px-3 py-1.5 rounded-full">
                    <Clock className="h-3 w-3" />
                    {new Date().toLocaleDateString("en-CA", { weekday: "long", month: "short", day: "numeric" })}
                </div>
            </div>

            {/* Filters: time window + service area */}
            <div className="flex flex-col gap-3">
                <div className="flex flex-wrap items-center gap-2">
                    {RANGES.map((r) => (
                        <Pill key={r.id} active={range === r.id} onClick={() => setRange(r.id)}>{r.label}</Pill>
                    ))}
                </div>
                <div className="flex flex-wrap items-center gap-2">
                    <Pill active={areaId === null} onClick={() => setAreaId(null)}>
                        <MapPin className="h-3 w-3" /> All areas
                    </Pill>
                    {areas.map((a) => (
                        <Pill key={a.id} active={areaId === a.id} onClick={() => setAreaId(a.id)}>{a.name || a.id}</Pill>
                    ))}
                </div>
            </div>

            {error && (
                <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    Dashboard data is temporarily unavailable. Check backend health and try again.
                </div>
            )}

            {loading && !data ? (
                <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                    {Array.from({ length: 8 }).map((_, i) => (
                        <div key={i} className="h-28 rounded-2xl bg-muted animate-pulse" />
                    ))}
                </div>
            ) : (
                <div className={`space-y-6 transition-opacity ${loading ? "opacity-60" : "opacity-100"}`}>
                    {/* Drivers + Riders */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        <StatCard icon={Wifi} label="Online Drivers" value={drivers.online} subtitle={`of ${(drivers.total ?? 0).toLocaleString()} registered`} color="emerald" pulse />
                        <StatCard icon={UserCheck} label="Active Drivers" value={drivers.active} subtitle="available now" color="blue" />
                        <StatCard icon={UserPlus} label="New Drivers" value={drivers.new} subtitle={rangeLabel.toLowerCase()} color="amber" />
                        <StatCard icon={Users} label="New Riders" value={riders.new} subtitle={`${(riders.total ?? 0).toLocaleString()} total`} color="violet" />
                    </div>

                    {/* Rides */}
                    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
                        <StatCard icon={Car} label="Rides" value={rides.total} subtitle={rangeLabel.toLowerCase()} color="sky" />
                        <StatCard icon={Activity} label="Active Now" value={rides.active} subtitle="in progress" color="emerald" pulse />
                        <StatCard icon={Car} label="Completed" value={bd.completed} subtitle={rangeLabel.toLowerCase()} color="teal" />
                        <StatCard icon={Car} label="Cancelled" value={bd.cancelled} subtitle={rangeLabel.toLowerCase()} color="red" />
                    </div>

                    {/* Money — Spinr is 0% commission: drivers keep 100% of fares. */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
                        <RevenueCard label="Platform Revenue" value={money.platform_revenue} disabled={aggOff} icon={DollarSign} color="from-red-500 to-rose-600" subtitle="Spinr Pass (0% on rides)" />
                        <RevenueCard label="Driver Earnings" value={money.driver_earnings} disabled={aggOff} icon={TrendingUp} color="from-emerald-500 to-teal-600" subtitle={`${formatCurrency(money.ride_volume ?? 0)} ride volume`} />
                        <RevenueCard label="Spinr Pass Earnings" value={money.spinr_pass_earnings} disabled={aggOff} icon={CreditCard} color="from-violet-500 to-purple-600" subtitle={rangeLabel.toLowerCase()} />
                    </div>

                    {aggOff && (
                        <div className="rounded-md border border-amber-200 bg-amber-50 px-4 py-2 text-xs text-amber-800">
                            Revenue figures need PostgREST aggregate functions enabled on the Supabase project (counts are unaffected).
                        </div>
                    )}

                    {/* Ride breakdown */}
                    <div className="bg-card border rounded-2xl p-5">
                        <h3 className="text-sm font-bold text-muted-foreground uppercase tracking-wider mb-4">
                            Ride Breakdown · {rangeLabel}
                        </h3>
                        <div className="space-y-3">
                            <BarStat label="Completed" value={bd.completed} total={rides.total} color="bg-emerald-500" />
                            <BarStat label="In progress" value={bd.in_progress} total={rides.total} color="bg-blue-500" />
                            <BarStat label="Searching" value={bd.searching} total={rides.total} color="bg-amber-500" />
                            <BarStat label="Scheduled" value={bd.scheduled} total={rides.total} color="bg-violet-500" />
                            <BarStat label="Cancelled" value={bd.cancelled} total={rides.total} color="bg-red-400" />
                        </div>
                        <div className="flex items-center gap-4 mt-4 pt-4 border-t">
                            <div className="flex-1">
                                <p className="text-xs text-muted-foreground">Completion rate</p>
                                <p className="text-lg font-bold text-emerald-500">
                                    {rides.total > 0 ? (((bd.completed ?? 0) / rides.total) * 100).toFixed(1) : 0}%
                                </p>
                            </div>
                            <div className="flex-1">
                                <p className="text-xs text-muted-foreground">Cancellation rate</p>
                                <p className="text-lg font-bold text-red-400">
                                    {rides.total > 0 ? (((bd.cancelled ?? 0) / rides.total) * 100).toFixed(1) : 0}%
                                </p>
                            </div>
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

// ─── Components ───

function Pill({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
    return (
        <button
            onClick={onClick}
            className={`inline-flex items-center gap-1 px-3 py-1.5 rounded-full text-xs font-medium transition-colors ${
                active ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground hover:bg-muted/70"
            }`}
        >
            {children}
        </button>
    );
}

// Static lookup avoids dynamic Tailwind class interpolation (bg-${color}-500/10),
// which is purged at build time and produces invisible icons in production.
const STAT_COLOR_CLASSES: Record<string, { bg: string; text: string }> = {
    blue:   { bg: "bg-blue-500/10",    text: "text-blue-500" },
    emerald: { bg: "bg-emerald-500/10", text: "text-emerald-500" },
    violet: { bg: "bg-violet-500/10",  text: "text-violet-500" },
    amber:  { bg: "bg-amber-500/10",   text: "text-amber-500" },
    red:    { bg: "bg-red-500/10",     text: "text-red-500" },
    teal:   { bg: "bg-teal-500/10",    text: "text-teal-500" },
    sky:    { bg: "bg-sky-500/10",     text: "text-sky-500" },
};

function StatCard({ icon: Icon, label, value, color, subtitle, pulse }: {
    icon: any; label: string; value: number | undefined; color: string; subtitle?: string; pulse?: boolean;
}) {
    const c = STAT_COLOR_CLASSES[color] ?? STAT_COLOR_CLASSES.blue;
    const v = value ?? 0;
    return (
        <div className="bg-card border rounded-2xl p-4 hover:shadow-md transition-shadow">
            <div className="flex items-center justify-between mb-3">
                <p className="text-xs font-medium text-muted-foreground">{label}</p>
                <div className={`w-8 h-8 rounded-lg ${c.bg} flex items-center justify-center`}>
                    <Icon className={`h-4 w-4 ${c.text}`} />
                </div>
            </div>
            <div className="flex items-end gap-2">
                <p className="text-2xl font-bold tracking-tight">{v.toLocaleString()}</p>
                {pulse && v > 0 && <span className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse mb-2" />}
            </div>
            {subtitle && <p className="text-[11px] text-muted-foreground mt-1">{subtitle}</p>}
        </div>
    );
}

function RevenueCard({ label, value, icon: Icon, color, subtitle, disabled }: {
    label: string; value: number | null | undefined; icon: any; color: string; subtitle?: string; disabled?: boolean;
}) {
    return (
        <div className={`bg-gradient-to-br ${color} rounded-2xl p-5 text-white`}>
            <div className="flex items-center gap-2 mb-3">
                <Icon className="h-5 w-5 text-white/80" />
                <p className="text-sm font-medium text-white/80">{label}</p>
            </div>
            <p className="text-3xl font-extrabold">{disabled || value == null ? "—" : formatCurrency(value)}</p>
            {subtitle && <p className="text-xs text-white/60 mt-1">{subtitle}</p>}
        </div>
    );
}

function BarStat({ label, value, total, color }: { label: string; value: number | undefined; total: number | undefined; color: string }) {
    const v = value ?? 0;
    const t = total ?? 0;
    const pct = t > 0 ? (v / t) * 100 : 0;
    return (
        <div>
            <div className="flex items-center justify-between mb-1">
                <p className="text-sm font-medium">{label}</p>
                <p className="text-sm font-bold">{v.toLocaleString()}</p>
            </div>
            <div className="h-2 bg-muted rounded-full overflow-hidden">
                <div className={`h-full ${color} rounded-full transition-all`} style={{ width: `${pct}%` }} />
            </div>
        </div>
    );
}
