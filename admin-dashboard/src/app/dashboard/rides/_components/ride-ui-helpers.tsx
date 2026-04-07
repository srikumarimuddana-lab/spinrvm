"use client";

import { formatCurrency } from "@/lib/utils";

export function Sec({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div>
            <h4 className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-2">{title}</h4>
            <div className="bg-muted/30 rounded-xl p-3 space-y-2">{children}</div>
        </div>
    );
}

export function FR({ l, v, b, t }: { l: string; v?: any; b?: boolean; t?: boolean }) {
    const d = t ? (v || "—") : formatCurrency(v || 0);
    return (
        <div className="flex justify-between text-sm">
            <span className="text-muted-foreground">{l}</span>
            <span className={b ? "font-bold" : "font-medium"}>{d}</span>
        </div>
    );
}

export function MStat({ label, value, icon: I }: { label: string; value: string; icon: any }) {
    return (
        <div className="flex-1 bg-background rounded-lg p-2 text-center">
            <I className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-1" />
            <p className="text-xs font-bold">{value}</p>
            <p className="text-[9px] text-muted-foreground">{label}</p>
        </div>
    );
}

export function TL({ l, t, d, km }: { l: string; t?: string; d?: boolean; km?: number }) {
    if (!t) return null;
    return (
        <div className="flex items-center gap-2.5">
            <div className={`w-1.5 h-1.5 rounded-full shrink-0 ${d ? "bg-red-400" : "bg-emerald-400"}`} />
            <p className="text-sm flex-1">{l}</p>
            {km != null && km > 0 && (
                <span className="text-[10px] font-semibold text-primary bg-primary/10 px-1.5 py-0.5 rounded">{km.toFixed(2)} km</span>
            )}
            <p className="text-[10px] text-muted-foreground">
                {new Date(t).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
            </p>
        </div>
    );
}

export function getStatusBadge(status: string) {
    const map: Record<string, string> = {
        completed: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400",
        cancelled: "bg-red-100 text-red-600 dark:bg-red-900/30 dark:text-red-400",
        in_progress: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400",
        searching: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400",
        driver_assigned: "bg-violet-100 text-violet-700 dark:bg-violet-900/30 dark:text-violet-400",
        driver_arrived: "bg-teal-100 text-teal-700 dark:bg-teal-900/30 dark:text-teal-400",
    };
    return (
        <span className={`px-2 py-0.5 rounded-md text-xs font-bold ${map[status] || "bg-gray-100 text-gray-600"}`}>
            {status?.replace(/_/g, " ").toUpperCase()}
        </span>
    );
}

export function fmtTime(d: string) {
    if (!d) return "—";
    try {
        return new Date(d).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
    } catch {
        return d;
    }
}

export function isRideLive(status: string) {
    return ["searching", "driver_assigned", "driver_arrived", "in_progress"].includes(status);
}

/** Haversine distance between two GPS points in km */
function haversine(lat1: number, lng1: number, lat2: number, lng2: number): number {
    const R = 6371;
    const dLat = ((lat2 - lat1) * Math.PI) / 180;
    const dLng = ((lng2 - lng1) * Math.PI) / 180;
    const a =
        Math.sin(dLat / 2) ** 2 +
        Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
    return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}

/** Compute distance per tracking phase from location trail */
export function computePhaseDistances(trail: { lat: number; lng: number; tracking_phase?: string }[]): { phase: string; distance_km: number; points: number }[] {
    if (!trail || trail.length < 2) return [];

    const phaseMap: Record<string, { distance: number; points: number }> = {};
    for (let i = 1; i < trail.length; i++) {
        const phase = trail[i].tracking_phase || "unknown";
        const d = haversine(trail[i - 1].lat, trail[i - 1].lng, trail[i].lat, trail[i].lng);
        if (!phaseMap[phase]) phaseMap[phase] = { distance: 0, points: 0 };
        phaseMap[phase].distance += d;
        phaseMap[phase].points += 1;
    }

    const order = ["navigating_to_pickup", "arrived_at_pickup", "trip_in_progress", "online_idle", "unknown"];
    return Object.entries(phaseMap)
        .sort(([a], [b]) => order.indexOf(a) - order.indexOf(b))
        .map(([phase, v]) => ({ phase, distance_km: Math.round(v.distance * 100) / 100, points: v.points + 1 }));
}
