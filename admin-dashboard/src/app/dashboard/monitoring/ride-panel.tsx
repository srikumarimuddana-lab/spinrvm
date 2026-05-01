// src/app/dashboard/monitoring/ride-panel.tsx
"use client";

import { MonitoringRide } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
    Banknote,
    Building2,
    Car,
    CheckCircle2,
    ChevronRight,
    Clock,
    Copy,
    DollarSign,
    Gauge,
    Loader2,
    MapPin,
    Navigation,
    Phone,
    Radio,
    Route,
    Search,
    Timer,
    User,
    XCircle,
} from "lucide-react";

interface RidePanelProps {
    ride: MonitoringRide;
    onDriverClick: (driverId: string) => void;
    onCancelRide: (rideId: string) => void;
    onCompleteRide?: (rideId: string) => void;
}

type StatusKey = "searching" | "driver_assigned" | "driver_arrived" | "in_progress";

const STATUS_STEPS: StatusKey[] = [
    "searching",
    "driver_assigned",
    "driver_arrived",
    "in_progress",
];

const STATUS_META: Record<
    StatusKey,
    { label: string; short: string; description: string; tone: string; ring: string; icon: React.ComponentType<{ className?: string }> }
> = {
    searching: {
        label: "Searching for Driver",
        short: "Searching",
        description: "Looking for the closest available driver in the area.",
        tone: "from-amber-500 to-orange-500",
        ring: "ring-amber-500/30",
        icon: Search,
    },
    driver_assigned: {
        label: "Driver En Route",
        short: "Assigned",
        description: "Driver accepted the trip and is heading to the pickup point.",
        tone: "from-blue-500 to-indigo-500",
        ring: "ring-blue-500/30",
        icon: Navigation,
    },
    driver_arrived: {
        label: "Driver Arrived",
        short: "Arrived",
        description: "Driver is at the pickup location waiting for the rider.",
        tone: "from-purple-500 to-fuchsia-500",
        ring: "ring-purple-500/30",
        icon: MapPin,
    },
    in_progress: {
        label: "Trip In Progress",
        short: "In Trip",
        description: "Rider is on board. Trip is actively underway.",
        tone: "from-emerald-500 to-green-500",
        ring: "ring-emerald-500/30",
        icon: Route,
    },
};

function fmtElapsed(minutes: number): { primary: string; unit: string } {
    if (minutes < 1) return { primary: "<1", unit: "min" };
    if (minutes < 60) return { primary: String(minutes), unit: "min" };
    const h = Math.floor(minutes / 60);
    const m = minutes % 60;
    return { primary: `${h}h ${m}m`, unit: "" };
}

export function RidePanel({ ride, onDriverClick, onCancelRide, onCompleteRide }: RidePanelProps) {
    const statusKey = (ride.status as StatusKey) in STATUS_META ? (ride.status as StatusKey) : "searching";
    const meta = STATUS_META[statusKey];
    const currentStepIdx = STATUS_STEPS.indexOf(statusKey);
    const elapsedMins = Math.max(
        0,
        Math.floor((Date.now() - new Date(ride.created_at).getTime()) / 60_000),
    );
    const elapsed = fmtElapsed(elapsedMins);
    const StatusIcon = meta.icon;

    function copyId() {
        navigator.clipboard.writeText(ride.id);
    }

    return (
        <div className="flex h-full flex-col bg-gradient-to-b from-muted/40 to-muted/20">
            {/* ───────── Hero Header ───────── */}
            <div className="relative shrink-0 overflow-hidden border-b bg-background">
                {/* Tonal accent bar */}
                <div className={`h-1 w-full bg-gradient-to-r ${meta.tone}`} />

                <div className="space-y-4 p-5">
                    {/* Top row — id + corporate */}
                    <div className="flex items-center justify-between gap-2">
                        <button
                            onClick={copyId}
                            className="group flex items-center gap-1.5 rounded-md bg-muted px-2 py-1 text-[11px] font-mono text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground"
                            title="Copy ride ID"
                        >
                            <span className="text-[9px] font-bold uppercase tracking-wider not-italic">Ride</span>
                            <span className="text-foreground">#{ride.id.slice(-8)}</span>
                            <Copy className="h-3 w-3 opacity-50 group-hover:opacity-100" />
                        </button>
                        {ride.is_corporate && (
                            <Badge
                                variant="outline"
                                className="gap-1 border-primary/40 bg-primary/5 text-[10px] font-bold uppercase tracking-wider text-primary"
                            >
                                <Building2 className="h-3 w-3" /> Corporate
                            </Badge>
                        )}
                    </div>

                    {/* Status hero */}
                    <div className="flex items-start gap-4">
                        <div className={`flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl bg-gradient-to-br ${meta.tone} text-white shadow-lg ring-4 ${meta.ring}`}>
                            <StatusIcon className="h-7 w-7" />
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className="flex items-center gap-2">
                                <span className="relative flex h-2 w-2">
                                    <span className={`absolute inline-flex h-full w-full animate-ping rounded-full bg-gradient-to-r ${meta.tone} opacity-75`} />
                                    <span className={`relative inline-flex h-2 w-2 rounded-full bg-gradient-to-r ${meta.tone}`} />
                                </span>
                                <span className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                    Live · Updated just now
                                </span>
                            </div>
                            <h2 className="mt-1 text-xl font-bold leading-tight text-foreground">
                                {meta.label}
                            </h2>
                            <p className="mt-1 text-xs text-muted-foreground">
                                Started {elapsedMins === 0 ? "just now" : `${elapsed.primary} ${elapsed.unit} ago`}
                            </p>
                        </div>
                    </div>

                    {/* Status description */}
                    <p className="rounded-lg bg-muted/60 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
                        {meta.description}
                    </p>
                </div>
            </div>

            {/* ───────── Scrollable Body ───────── */}
            <div className="flex-1 space-y-4 overflow-y-auto p-4 pb-28">
                {/* Quick stats — at-a-glance numbers */}
                <div className="grid grid-cols-3 gap-2.5">
                    <StatCard
                        icon={Timer}
                        value={elapsed.primary}
                        unit={elapsed.unit}
                        label="Elapsed"
                        tone="default"
                    />
                    <StatCard
                        icon={Gauge}
                        value={ride.distance_km ? ride.distance_km.toFixed(1) : "—"}
                        unit={ride.distance_km ? "km" : ""}
                        label="Distance"
                        tone="default"
                    />
                    <StatCard
                        icon={DollarSign}
                        value={ride.total_fare ? `$${ride.total_fare.toFixed(2)}` : "—"}
                        unit=""
                        label="Total Fare"
                        tone="success"
                    />
                </div>

                {/* Trip Progress */}
                <Card>
                    <CardHeader icon={Clock} title="Trip Progress" />
                    <div className="px-5 pb-5">
                        <div className="relative flex items-center justify-between px-1">
                            {STATUS_STEPS.map((step, i) => {
                                const stepMeta = STATUS_META[step];
                                const isActive = i <= currentStepIdx;
                                const isCurrent = i === currentStepIdx;
                                return (
                                    <div key={step} className="z-10 flex flex-col items-center gap-2">
                                        <div
                                            className={`relative flex h-8 w-8 items-center justify-center rounded-full ring-4 ring-card transition-all duration-300 ${
                                                isActive
                                                    ? `bg-gradient-to-br ${stepMeta.tone} text-white shadow-md`
                                                    : "bg-muted text-muted-foreground"
                                            } ${isCurrent ? "scale-110" : ""}`}
                                        >
                                            {isCurrent ? (
                                                <span className={`absolute -inset-1 animate-ping rounded-full bg-gradient-to-br ${stepMeta.tone} opacity-30`} />
                                            ) : null}
                                            {isActive && !isCurrent ? (
                                                <CheckCircle2 className="h-4 w-4" />
                                            ) : (
                                                <div className="h-2 w-2 rounded-full bg-current opacity-90" />
                                            )}
                                        </div>
                                        <span
                                            className={`text-[10px] font-bold uppercase tracking-wider ${
                                                isActive ? "text-foreground" : "text-muted-foreground/60"
                                            }`}
                                        >
                                            {stepMeta.short}
                                        </span>
                                    </div>
                                );
                            })}
                            {/* Connecting bar */}
                            <div className="absolute left-2 right-2 top-4 -z-0 h-1 overflow-hidden rounded-full bg-muted">
                                <div
                                    className={`h-full bg-gradient-to-r ${meta.tone} transition-all duration-500 ease-out`}
                                    style={{
                                        width: `${(currentStepIdx / (STATUS_STEPS.length - 1)) * 100}%`,
                                    }}
                                />
                            </div>
                        </div>
                    </div>
                </Card>

                {/* Route */}
                <Card>
                    <CardHeader icon={Route} title="Route" />
                    <div className="px-5 pb-5">
                        <div className="relative space-y-5 pl-7">
                            {/* Vertical connecting line */}
                            <div className="absolute left-[10px] top-3 bottom-3 w-px border-l-2 border-dashed border-muted-foreground/30" />

                            {/* Pickup */}
                            <div className="relative">
                                <div className="absolute -left-7 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-blue-500/15 ring-4 ring-card">
                                    <div className="h-2 w-2 rounded-full bg-blue-600 ring-2 ring-blue-200" />
                                </div>
                                <p className="text-[10px] font-bold uppercase tracking-wider text-blue-600">
                                    Pickup
                                </p>
                                <p className="mt-1 text-sm font-medium leading-snug text-foreground">
                                    {ride.pickup_address ??
                                        `${ride.pickup_lat?.toFixed(4)}, ${ride.pickup_lng?.toFixed(4)}`}
                                </p>
                            </div>

                            {/* Dropoff */}
                            <div className="relative">
                                <div className="absolute -left-7 top-0.5 flex h-5 w-5 items-center justify-center rounded-full bg-red-500/15 ring-4 ring-card">
                                    <div className="h-2 w-2 rounded-full bg-red-600 ring-2 ring-red-200" />
                                </div>
                                <p className="text-[10px] font-bold uppercase tracking-wider text-red-600">
                                    Dropoff
                                </p>
                                <p className="mt-1 text-sm font-medium leading-snug text-foreground">
                                    {ride.dropoff_address ??
                                        `${ride.dropoff_lat?.toFixed(4)}, ${ride.dropoff_lng?.toFixed(4)}`}
                                </p>
                            </div>
                        </div>
                    </div>
                </Card>

                {/* Participants */}
                <Card>
                    <CardHeader icon={User} title="Participants" />
                    <div className="px-3 pb-3 pt-1">
                        {/* Rider */}
                        <div className="group flex items-center gap-3 rounded-lg p-2.5 transition-colors hover:bg-muted/40">
                            <div className="relative shrink-0">
                                <Avatar className="h-11 w-11 border-2 border-background shadow-sm">
                                    <AvatarFallback className="bg-blue-100 text-sm font-bold text-blue-700">
                                        {ride.rider_name.slice(0, 2).toUpperCase()}
                                    </AvatarFallback>
                                </Avatar>
                                <div className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-blue-500 ring-2 ring-card">
                                    <User className="h-2.5 w-2.5 text-white" />
                                </div>
                            </div>
                            <div className="min-w-0 flex-1">
                                <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                    Rider
                                </p>
                                <p className="truncate text-sm font-semibold text-foreground">
                                    {ride.rider_name}
                                </p>
                                {ride.rider_phone && (
                                    <p className="truncate text-[11px] font-mono text-muted-foreground">
                                        {ride.rider_phone}
                                    </p>
                                )}
                            </div>
                            {ride.rider_phone && (
                                <Button
                                    size="icon"
                                    variant="outline"
                                    className="h-9 w-9 rounded-full shadow-sm transition-colors hover:border-blue-200 hover:bg-blue-50 hover:text-blue-600"
                                    onClick={() => window.open(`tel:${ride.rider_phone}`)}
                                    title="Call rider"
                                >
                                    <Phone className="h-3.5 w-3.5" />
                                </Button>
                            )}
                        </div>

                        <div className="my-1 mx-2 h-px bg-border/60" />

                        {/* Driver */}
                        {ride.driver_id ? (
                            <div
                                role="button"
                                tabIndex={0}
                                className="group flex cursor-pointer items-center gap-3 rounded-lg p-2.5 transition-all hover:bg-muted/60 active:scale-[0.99]"
                                onClick={() => ride.driver_id && onDriverClick(ride.driver_id)}
                            >
                                <div className="relative shrink-0">
                                    <Avatar className="h-11 w-11 border-2 border-background shadow-sm">
                                        <AvatarFallback className="bg-purple-100 text-sm font-bold text-purple-700">
                                            {(ride.driver_name ?? "DR").slice(0, 2).toUpperCase()}
                                        </AvatarFallback>
                                    </Avatar>
                                    <div className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-purple-500 ring-2 ring-card">
                                        <Car className="h-2.5 w-2.5 text-white" />
                                    </div>
                                </div>
                                <div className="min-w-0 flex-1">
                                    <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">
                                        Driver
                                    </p>
                                    <p className="truncate text-sm font-semibold text-foreground">
                                        {ride.driver_name ?? "Driver assigned"}
                                    </p>
                                    {ride.driver_phone && (
                                        <p className="truncate text-[11px] font-mono text-muted-foreground">
                                            {ride.driver_phone}
                                        </p>
                                    )}
                                </div>
                                <div className="flex items-center gap-1">
                                    {ride.driver_phone && (
                                        <Button
                                            size="icon"
                                            variant="outline"
                                            className="h-9 w-9 rounded-full shadow-sm transition-colors hover:border-purple-200 hover:bg-purple-50 hover:text-purple-600"
                                            onClick={(e) => {
                                                e.stopPropagation();
                                                window.open(`tel:${ride.driver_phone}`);
                                            }}
                                            title="Call driver"
                                        >
                                            <Phone className="h-3.5 w-3.5" />
                                        </Button>
                                    )}
                                    <ChevronRight className="h-5 w-5 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
                                </div>
                            </div>
                        ) : (
                            <div className="flex items-center gap-3 rounded-lg border border-dashed border-amber-300 bg-amber-50/50 p-3 text-amber-700">
                                <Loader2 className="h-4 w-4 animate-spin" />
                                <div className="text-xs">
                                    <p className="font-semibold">Searching for a driver…</p>
                                    <p className="text-[11px] text-amber-600/80">
                                        No driver matched yet. Dispatch is still scanning.
                                    </p>
                                </div>
                            </div>
                        )}
                    </div>
                </Card>

                {/* Footer hint */}
                <p className="px-1 pt-1 text-center text-[10px] text-muted-foreground/70">
                    <Radio className="mr-1 inline h-3 w-3" />
                    Live monitoring · refreshes automatically
                </p>
            </div>

            {/* ───────── Sticky Action Bar ───────── */}
            <div className="absolute bottom-0 left-0 right-0 border-t bg-background/95 p-3 shadow-[0_-12px_32px_-12px_rgba(0,0,0,0.12)] backdrop-blur-md">
                <div className="flex items-stretch gap-2.5">
                    <Button
                        variant="destructive"
                        className="h-11 flex-1 gap-2 font-bold shadow-sm"
                        onClick={() => onCancelRide(ride.id)}
                    >
                        <XCircle className="h-4 w-4" /> Cancel
                    </Button>
                    {onCompleteRide && (
                        <Button
                            className="h-11 flex-1 gap-2 bg-emerald-600 font-bold text-white shadow-sm hover:bg-emerald-700"
                            onClick={() => {
                                if (
                                    window.confirm(
                                        "Force-complete this ride? Driver will be freed and the rider's app will exit the active-ride flow.",
                                    )
                                ) {
                                    onCompleteRide(ride.id);
                                }
                            }}
                        >
                            <CheckCircle2 className="h-4 w-4" /> Complete
                        </Button>
                    )}
                </div>
                <p className="mt-2 text-center text-[10px] text-muted-foreground/70">
                    Admin override · audit-logged
                </p>
            </div>
        </div>
    );
}

// ───────── Local building blocks ─────────

function Card({ children }: { children: React.ReactNode }) {
    return (
        <div className="overflow-hidden rounded-xl border bg-card shadow-sm">
            {children}
        </div>
    );
}

function CardHeader({
    icon: Icon,
    title,
}: {
    icon: React.ComponentType<{ className?: string }>;
    title: string;
}) {
    return (
        <div className="flex items-center gap-2 border-b bg-muted/30 px-5 py-2.5">
            <Icon className="h-3.5 w-3.5 text-muted-foreground" />
            <p className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                {title}
            </p>
        </div>
    );
}

function StatCard({
    icon: Icon,
    value,
    unit,
    label,
    tone,
}: {
    icon: React.ComponentType<{ className?: string }>;
    value: string;
    unit: string;
    label: string;
    tone: "default" | "success";
}) {
    const isSuccess = tone === "success";
    return (
        <div
            className={`flex flex-col items-center justify-center rounded-xl border p-3 shadow-sm transition-colors ${
                isSuccess
                    ? "bg-emerald-50/60 ring-1 ring-inset ring-emerald-500/30 hover:bg-emerald-50"
                    : "bg-card hover:bg-muted/30"
            }`}
        >
            <Icon
                className={`mb-1.5 h-4 w-4 ${
                    isSuccess ? "text-emerald-600" : "text-muted-foreground"
                }`}
            />
            <p
                className={`text-lg font-black tracking-tight ${
                    isSuccess ? "text-emerald-700" : "text-foreground"
                }`}
            >
                {value}
                {unit && (
                    <span
                        className={`ml-0.5 text-xs font-medium ${
                            isSuccess ? "text-emerald-700/70" : "text-muted-foreground"
                        }`}
                    >
                        {unit}
                    </span>
                )}
            </p>
            <p
                className={`mt-0.5 text-[10px] font-bold uppercase tracking-wider ${
                    isSuccess ? "text-emerald-700/80" : "text-muted-foreground"
                }`}
            >
                {label}
            </p>
        </div>
    );
}
