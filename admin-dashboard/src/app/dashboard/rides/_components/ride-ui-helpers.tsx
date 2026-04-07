"use client";

import { formatCurrency } from "@/lib/utils";

export function Sec({ title, children, actions }: { title: string; children: React.ReactNode; actions?: React.ReactNode }) {
    return (
        <div>
            <div className="flex items-center justify-between mb-2.5">
                <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">{title}</h4>
                {actions}
            </div>
            <div className="bg-muted/30 rounded-xl p-3.5 space-y-2">{children}</div>
        </div>
    );
}

export function FR({ l, v, b, t }: { l: string; v?: any; b?: boolean; t?: boolean }) {
    const d = t ? (v || "—") : formatCurrency(v || 0);
    return (
        <div className="flex justify-between text-sm items-center">
            <span className="text-muted-foreground">{l}</span>
            <span className={b ? "font-bold text-foreground" : "font-medium"}>{d}</span>
        </div>
    );
}

export function MStat({ label, value, icon: I }: { label: string; value: string; icon: any }) {
    return (
        <div className="flex-1 bg-background rounded-lg p-2.5 text-center">
            <I className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-1" />
            <p className="text-xs font-bold">{value}</p>
            <p className="text-[9px] text-muted-foreground">{label}</p>
        </div>
    );
}

export function TL({ l, t, d, km }: { l: string; t?: string; d?: boolean; km?: number }) {
    if (!t) return null;
    return (
        <div className="flex items-center gap-2.5 py-1">
            <div className={`w-2 h-2 rounded-full shrink-0 ring-2 ring-offset-1 ring-offset-background ${
                d ? "bg-red-400 ring-red-200 dark:ring-red-900/50" : "bg-emerald-400 ring-emerald-200 dark:ring-emerald-900/50"
            }`} />
            <p className="text-sm flex-1 font-medium">{l}</p>
            {km != null && km > 0 && (
                <span className="text-[10px] font-semibold text-primary bg-primary/10 px-1.5 py-0.5 rounded-md">{km.toFixed(2)} km</span>
            )}
            <p className="text-[11px] text-muted-foreground tabular-nums">
                {new Date(t).toLocaleString("en-CA", { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" })}
            </p>
        </div>
    );
}

const STATUS_CONFIG: Record<string, { bg: string; text: string; dot: string; label: string }> = {
    completed: {
        bg: "bg-emerald-50 dark:bg-emerald-900/20",
        text: "text-emerald-700 dark:text-emerald-400",
        dot: "bg-emerald-500",
        label: "Completed",
    },
    cancelled: {
        bg: "bg-red-50 dark:bg-red-900/20",
        text: "text-red-600 dark:text-red-400",
        dot: "bg-red-500",
        label: "Cancelled",
    },
    in_progress: {
        bg: "bg-blue-50 dark:bg-blue-900/20",
        text: "text-blue-700 dark:text-blue-400",
        dot: "bg-blue-500",
        label: "In Progress",
    },
    searching: {
        bg: "bg-amber-50 dark:bg-amber-900/20",
        text: "text-amber-700 dark:text-amber-400",
        dot: "bg-amber-500 animate-pulse",
        label: "Searching",
    },
    driver_assigned: {
        bg: "bg-violet-50 dark:bg-violet-900/20",
        text: "text-violet-700 dark:text-violet-400",
        dot: "bg-violet-500",
        label: "Assigned",
    },
    driver_arrived: {
        bg: "bg-teal-50 dark:bg-teal-900/20",
        text: "text-teal-700 dark:text-teal-400",
        dot: "bg-teal-500",
        label: "Arrived",
    },
};

export function getStatusBadge(status: string) {
    const config = STATUS_CONFIG[status] || {
        bg: "bg-gray-50 dark:bg-gray-900/20",
        text: "text-gray-600 dark:text-gray-400",
        dot: "bg-gray-400",
        label: status?.replace(/_/g, " "),
    };

    return (
        <span className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-bold ${config.bg} ${config.text}`}>
            <span className={`w-1.5 h-1.5 rounded-full ${config.dot}`} />
            {config.label}
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
