"use client";

import { useEffect, useState } from "react";
import { getDriverActivity } from "@/lib/api";
import {
    UserPlus, FileText, CheckCircle, XCircle, ShieldCheck, ShieldAlert,
    Ban, Pause, Play, AlertTriangle, StickyNote, CreditCard, Car,
    Wifi, WifiOff, Settings, Clock, Loader2, ChevronDown, ChevronUp,
} from "lucide-react";

const EVENT_CONFIG: Record<string, { icon: any; color: string; bg: string; pipeColor: string }> = {
    registered:             { icon: UserPlus,     color: "text-blue-600",    bg: "bg-blue-100 dark:bg-blue-900/30",    pipeColor: "border-blue-300" },
    document_uploaded:      { icon: FileText,     color: "text-violet-600",  bg: "bg-violet-100 dark:bg-violet-900/30",pipeColor: "border-violet-300" },
    document_approved:      { icon: CheckCircle,  color: "text-emerald-600", bg: "bg-emerald-100 dark:bg-emerald-900/30", pipeColor: "border-emerald-300" },
    document_rejected:      { icon: XCircle,      color: "text-red-600",     bg: "bg-red-100 dark:bg-red-900/30",      pipeColor: "border-red-300" },
    approve:                { icon: ShieldCheck,  color: "text-emerald-600", bg: "bg-emerald-100 dark:bg-emerald-900/30", pipeColor: "border-emerald-400" },
    verified:               { icon: ShieldCheck,  color: "text-emerald-600", bg: "bg-emerald-100 dark:bg-emerald-900/30", pipeColor: "border-emerald-400" },
    reject:                 { icon: XCircle,      color: "text-red-600",     bg: "bg-red-100 dark:bg-red-900/30",      pipeColor: "border-red-400" },
    suspend:                { icon: Pause,        color: "text-orange-600",  bg: "bg-orange-100 dark:bg-orange-900/30",pipeColor: "border-orange-400" },
    ban:                    { icon: Ban,          color: "text-red-700",     bg: "bg-red-200 dark:bg-red-900/40",      pipeColor: "border-red-500" },
    unban:                  { icon: Play,         color: "text-emerald-600", bg: "bg-emerald-100 dark:bg-emerald-900/30", pipeColor: "border-emerald-400" },
    reactivate:             { icon: Play,         color: "text-emerald-600", bg: "bg-emerald-100 dark:bg-emerald-900/30", pipeColor: "border-emerald-400" },
    status_override:        { icon: Settings,     color: "text-purple-600",  bg: "bg-purple-100 dark:bg-purple-900/30",pipeColor: "border-purple-400" },
    profile_updated:        { icon: UserPlus,     color: "text-blue-600",    bg: "bg-blue-100 dark:bg-blue-900/30",    pipeColor: "border-blue-300" },
    vehicle_updated:        { icon: Car,          color: "text-blue-600",    bg: "bg-blue-100 dark:bg-blue-900/30",    pipeColor: "border-blue-300" },
    went_online:            { icon: Wifi,         color: "text-emerald-500", bg: "bg-emerald-50 dark:bg-emerald-900/20",pipeColor: "border-emerald-200" },
    went_offline:           { icon: WifiOff,      color: "text-gray-500",    bg: "bg-gray-100 dark:bg-gray-800/30",    pipeColor: "border-gray-300" },
    note_added:             { icon: StickyNote,   color: "text-amber-600",   bg: "bg-amber-100 dark:bg-amber-900/30",  pipeColor: "border-amber-300" },
    subscription_started:   { icon: CreditCard,   color: "text-violet-600",  bg: "bg-violet-100 dark:bg-violet-900/30",pipeColor: "border-violet-300" },
    subscription_cancelled: { icon: CreditCard,   color: "text-gray-500",    bg: "bg-gray-100 dark:bg-gray-800/30",    pipeColor: "border-gray-300" },
    ride_completed:         { icon: Car,          color: "text-emerald-600", bg: "bg-emerald-50 dark:bg-emerald-900/20",pipeColor: "border-emerald-200" },
};

const DEFAULT_CONFIG = { icon: AlertTriangle, color: "text-gray-500", bg: "bg-gray-100 dark:bg-gray-800/30", pipeColor: "border-gray-300" };

function fmtDateTime(d: string) {
    if (!d) return "";
    try {
        return new Date(d).toLocaleString("en-CA", {
            month: "short", day: "numeric", year: "numeric",
            hour: "2-digit", minute: "2-digit",
        });
    } catch { return d; }
}

function fmtDate(d: string) {
    if (!d) return "";
    try { return new Date(d).toLocaleDateString("en-CA", { month: "long", day: "numeric", year: "numeric" }); }
    catch { return d; }
}

function groupByDate(items: any[]): Record<string, any[]> {
    const groups: Record<string, any[]> = {};
    for (const item of items) {
        const date = item.created_at ? new Date(item.created_at).toISOString().split("T")[0] : "unknown";
        (groups[date] = groups[date] || []).push(item);
    }
    return groups;
}

export default function DriverTimeline({ driverId, driver }: { driverId: string; driver: any }) {
    const [activities, setActivities] = useState<any[]>([]);
    const [loading, setLoading] = useState(true);
    const [expanded, setExpanded] = useState<Record<string, boolean>>({});

    useEffect(() => {
        setLoading(true);
        getDriverActivity(driverId)
            .then(data => {
                const items = Array.isArray(data) ? data : [];
                // Add synthetic events from driver record
                const synthetic: any[] = [];
                if (driver?.created_at) {
                    synthetic.push({
                        id: "syn_registered",
                        event_type: "registered",
                        title: "Driver Registered",
                        description: `${driver.first_name || ""} ${driver.last_name || ""} created their driver profile`,
                        created_at: driver.created_at,
                        actor: "driver",
                    });
                }
                if (driver?.verified_at) {
                    synthetic.push({
                        id: "syn_verified",
                        event_type: "verified",
                        title: "Driver Verified",
                        description: "Admin approved the driver",
                        created_at: driver.verified_at,
                        actor: "admin",
                    });
                }
                if (driver?.suspended_at) {
                    synthetic.push({
                        id: "syn_suspended",
                        event_type: "suspend",
                        title: "Driver Suspended",
                        description: driver.suspension_reason || "",
                        created_at: driver.suspended_at,
                        actor: "admin",
                    });
                }
                if (driver?.banned_at) {
                    synthetic.push({
                        id: "syn_banned",
                        event_type: "ban",
                        title: "Driver Banned",
                        description: driver.ban_reason || "",
                        created_at: driver.banned_at,
                        actor: "admin",
                    });
                }
                // Merge and deduplicate by comparing timestamps
                const existing = new Set(items.map(i => `${i.event_type}_${i.created_at}`));
                const merged = [...items];
                for (const s of synthetic) {
                    if (!existing.has(`${s.event_type}_${s.created_at}`)) merged.push(s);
                }
                merged.sort((a, b) => (b.created_at || "").localeCompare(a.created_at || ""));
                setActivities(merged);
            })
            .catch(() => setActivities([]))
            .finally(() => setLoading(false));
    }, [driverId]);

    const toggleDate = (date: string) => setExpanded(prev => ({ ...prev, [date]: !prev[date] }));

    if (loading) {
        return (
            <div className="flex items-center justify-center py-10">
                <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
            </div>
        );
    }

    if (activities.length === 0) {
        return (
            <div className="text-center py-10 bg-muted/20 rounded-xl border border-dashed">
                <Clock className="h-8 w-8 mx-auto mb-2 text-muted-foreground/30" />
                <p className="text-sm text-muted-foreground">No activity recorded yet</p>
                <p className="text-xs text-muted-foreground/70 mt-0.5">Actions like approvals, suspensions, and document reviews will appear here</p>
            </div>
        );
    }

    const groups = groupByDate(activities);
    const dates = Object.keys(groups).sort((a, b) => b.localeCompare(a));

    return (
        <div className="space-y-1">
            <div className="flex items-center gap-2 mb-4">
                <Clock className="h-4 w-4 text-muted-foreground" />
                <h4 className="text-sm font-semibold">Activity Timeline</h4>
                <span className="text-[10px] text-muted-foreground bg-muted px-2 py-0.5 rounded-full">{activities.length} events</span>
            </div>

            {dates.map((date, dateIdx) => {
                const events = groups[date];
                const isExpanded = expanded[date] !== false; // default expanded
                const isToday = date === new Date().toISOString().split("T")[0];

                return (
                    <div key={date}>
                        {/* Date header */}
                        <button onClick={() => toggleDate(date)}
                            className="flex items-center gap-2 w-full text-left py-2 px-2 rounded-lg hover:bg-muted/50 transition group">
                            <div className={`w-3 h-3 rounded-full border-2 ${isToday ? "bg-primary border-primary" : "bg-muted border-muted-foreground/30"}`} />
                            <span className={`text-xs font-bold ${isToday ? "text-primary" : "text-muted-foreground"}`}>
                                {isToday ? "Today" : fmtDate(date)}
                            </span>
                            <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">{events.length}</span>
                            <div className="flex-1" />
                            {isExpanded ? <ChevronUp className="h-3.5 w-3.5 text-muted-foreground/50" /> : <ChevronDown className="h-3.5 w-3.5 text-muted-foreground/50" />}
                        </button>

                        {/* Events for this date */}
                        {isExpanded && (
                            <div className="ml-[5px] pl-5 border-l-2 border-muted-foreground/10 space-y-0">
                                {events.map((event, i) => {
                                    const cfg = EVENT_CONFIG[event.event_type] || DEFAULT_CONFIG;
                                    const Icon = cfg.icon;
                                    const meta = event.metadata || {};

                                    return (
                                        <div key={event.id} className="relative py-2 group">
                                            {/* Pipe connector dot */}
                                            <div className={`absolute -left-[23px] top-3.5 w-3 h-3 rounded-full border-2 ${cfg.pipeColor} ${cfg.bg}`} />

                                            <div className="flex items-start gap-3 pl-1">
                                                <div className={`w-8 h-8 rounded-lg flex items-center justify-center shrink-0 ${cfg.bg}`}>
                                                    <Icon className={`h-4 w-4 ${cfg.color}`} />
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex items-center gap-2">
                                                        <p className="text-sm font-semibold">{event.title}</p>
                                                        {event.actor && event.actor !== "system" && (
                                                            <span className="text-[10px] text-muted-foreground bg-muted px-1.5 py-0.5 rounded">
                                                                {event.actor}
                                                            </span>
                                                        )}
                                                    </div>
                                                    {event.description && (
                                                        <p className="text-xs text-muted-foreground mt-0.5">{event.description}</p>
                                                    )}
                                                    {/* Show status change metadata */}
                                                    {meta.old_status && meta.new_status && meta.old_status !== meta.new_status && (
                                                        <div className="flex items-center gap-1.5 mt-1">
                                                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-muted text-muted-foreground">{meta.old_status}</span>
                                                            <span className="text-[10px] text-muted-foreground">&rarr;</span>
                                                            <span className={`text-[10px] px-1.5 py-0.5 rounded font-semibold ${
                                                                meta.new_status === "active" ? "bg-emerald-100 text-emerald-700" :
                                                                meta.new_status === "banned" ? "bg-red-100 text-red-700" :
                                                                meta.new_status === "suspended" ? "bg-orange-100 text-orange-700" :
                                                                meta.new_status === "rejected" ? "bg-red-100 text-red-700" :
                                                                "bg-amber-100 text-amber-700"
                                                            }`}>{meta.new_status}</span>
                                                        </div>
                                                    )}
                                                    <p className="text-[10px] text-muted-foreground/60 mt-1 flex items-center gap-1">
                                                        <Clock className="h-2.5 w-2.5" />
                                                        {fmtDateTime(event.created_at)}
                                                    </p>
                                                </div>
                                            </div>
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                );
            })}
        </div>
    );
}
