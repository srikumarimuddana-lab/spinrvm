"use client";

import { useEffect, useState, useCallback } from "react";
import { getRideDetails, adminCancelRide } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
    Car, User, Phone, Mail, Star, Route, Clock, Percent,
    DollarSign, Receipt, Ticket, AlertTriangle, Flag, Radio,
    FileWarning, MapPinned, CalendarDays, Hash,
    Gauge, Shield, Users, MapPin, CheckCircle2, XCircle,
    TrendingUp, CreditCard, Ban, MessageSquare,
} from "lucide-react";
import { Sec, FR, MStat, TL, getStatusBadge, fmtTime, isRideLive, computePhaseDistances } from "./ride-ui-helpers";
import RideInvoice from "./ride-invoice";
import RideLostFound from "./ride-lost-found";
import RideFlagForm from "./ride-flag-form";
import RideComplaintForm from "./ride-complaint-form";
import dynamic from "next/dynamic";
import { routeQualityLabel } from "@spinr/shared/utils/routeSegments";

const RideRouteMap = dynamic(() => import("./ride-route-map"), { ssr: false });

// ── Status meta ──────────────────────────────────────────────────────────────
// Categorical ride-state hero badge (the 7 states from CLAUDE.md's ride
// state machine) — not a #2816 migration target. A gradient fill is a
// deliberate, self-contained visual element regardless of page theme, and
// a 3-token semantic system (success/warning/destructive) can't express 7
// distinct lifecycle states (driver_assigned/driver_accepted/driver_arrived
// have no success/warning/destructive equivalent).
/* eslint-disable no-restricted-syntax -- categorical ride-state hero badge, see comment above (#2816) */
const STATUS_META: Record<string, { label: string; gradient: string; ring: string; icon: React.ElementType }> = {
    searching:        { label: "Searching",        gradient: "from-amber-500 to-orange-500",    ring: "ring-amber-400",   icon: Route },
    driver_assigned:  { label: "Driver Assigned",  gradient: "from-blue-500 to-cyan-500",       ring: "ring-blue-400",    icon: Car },
    driver_accepted:  { label: "Driver Accepted",  gradient: "from-violet-500 to-purple-500",   ring: "ring-violet-400",  icon: Car },
    driver_arrived:   { label: "Driver Arrived",   gradient: "from-indigo-500 to-blue-500",     ring: "ring-indigo-400",  icon: MapPin },
    in_progress:      { label: "In Progress",      gradient: "from-emerald-500 to-teal-500",    ring: "ring-emerald-400", icon: TrendingUp },
    completed:        { label: "Completed",         gradient: "from-green-500 to-emerald-600",   ring: "ring-green-400",   icon: CheckCircle2 },
    cancelled:        { label: "Cancelled",         gradient: "from-red-500 to-rose-600",        ring: "ring-red-400",     icon: XCircle },
};
/* eslint-enable no-restricted-syntax */

const PHASE_COLORS: Record<string, string> = {
    navigating_to_pickup: "text-blue-600 bg-blue-50 dark:bg-blue-900/30 dark:text-blue-400",
    arrived_at_pickup:    "text-violet-600 bg-violet-50 dark:bg-violet-900/30 dark:text-violet-400",
    trip_in_progress:     "text-emerald-600 bg-emerald-50 dark:bg-emerald-900/30 dark:text-emerald-400",
    online_idle:          "text-gray-600 bg-gray-50 dark:bg-gray-800/50 dark:text-gray-400",
    unknown:              "text-gray-500 bg-gray-50 dark:bg-gray-800/30 dark:text-gray-500",
};

// ── Small reusable card wrapper ───────────────────────────────────────────────
function Card({ children, className = "" }: { children: React.ReactNode; className?: string }) {
    return (
        <div className={`rounded-xl border bg-card shadow-sm ${className}`}>
            {children}
        </div>
    );
}

function CardHead({ title, icon: Icon, accent }: { title: string; icon?: React.ElementType; accent?: string }) {
    return (
        <div className={`flex items-center gap-2 px-4 py-3 border-b ${accent || "bg-muted/30"} rounded-t-xl`}>
            {Icon && <Icon className="h-3.5 w-3.5 text-muted-foreground shrink-0" />}
            <p className="text-[11px] font-bold uppercase tracking-widest text-muted-foreground">{title}</p>
        </div>
    );
}

function StatPill({ label, value, icon: Icon, color = "bg-muted/60 text-foreground" }: {
    label: string; value: string; icon: React.ElementType; color?: string;
}) {
    return (
        <div className={`flex flex-col items-center justify-center rounded-xl px-4 py-3 ${color} flex-1 min-w-0`}>
            <Icon className="h-4 w-4 mb-1 opacity-70" />
            <p className="text-sm font-extrabold tabular-nums leading-none">{value}</p>
            <p className="text-[10px] opacity-60 mt-0.5">{label}</p>
        </div>
    );
}

interface Props {
    rideId: string | null;
    open: boolean;
    onClose: () => void;
}

export default function RideDetailModal({ rideId, open, onClose }: Props) {
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [flagTarget, setFlagTarget] = useState<{ type: "rider" | "driver"; name: string } | null>(null);
    const [showComplaint, setShowComplaint] = useState(false);
    const [showCancelDialog, setShowCancelDialog] = useState(false);
    const [cancelReason, setCancelReason] = useState("Cancelled by admin");
    const [cancelling, setCancelling] = useState(false);
    const [selectedPhase, setSelectedPhase] = useState<"pickup" | "actual" | "planned">("actual");

    const loadRide = useCallback(async () => {
        if (!rideId) return;
        setLoading(true);
        setLoadError(null);
        try {
            const data = await getRideDetails(rideId);
            setRide(data);
        } catch (err) {
            console.error('[RideDetailModal] Failed to load ride:', err);
            setLoadError('Failed to load ride details. Please try again.');
        } finally { setLoading(false); }
    }, [rideId]);

    useEffect(() => {
        if (open && rideId) loadRide();
        if (!open) setRide(null);
        setSelectedPhase("actual");
    }, [open, rideId, loadRide]);

    if (!open) return null;

    // Legacy-imported rides (migrated from the previous app) carry a planned
    // route and a booked distance only — no Phase 2/3 GPS, no v2 segments, no
    // measured distances. Without this flag the GPS views fall through to the
    // map's straight-line fallback, which draws a pickup→dropoff line that
    // reads as a real route. Say "never captured" instead of inventing one.
    const isImported = !!ride?.legacy_import_metadata
        && Object.keys(ride.legacy_import_metadata).length > 0;

    const storedPhases: Record<string, number> | null = ride?.phase_distances && Object.keys(ride.phase_distances).length > 0
        ? ride.phase_distances : null;

    const phaseDistances = storedPhases
        ? Object.entries(storedPhases).map(([phase, km]) => ({ phase, distance_km: Number(km) || 0, points: 0 }))
        : (ride?.location_trail ? computePhaseDistances(ride.location_trail) : []);

    const phaseMap: Record<string, number> = {};
    for (const p of phaseDistances) phaseMap[p.phase] = p.distance_km;

    const haversine = (lat1: number, lng1: number, lat2: number, lng2: number) => {
        const R = 6371;
        const dLat = ((lat2 - lat1) * Math.PI) / 180;
        const dLng = ((lng2 - lng1) * Math.PI) / 180;
        const a = Math.sin(dLat / 2) ** 2 + Math.cos((lat1 * Math.PI) / 180) * Math.cos((lat2 * Math.PI) / 180) * Math.sin(dLng / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(a));
    };
    if (ride && !phaseMap.navigating_to_pickup) {
        if (ride.pickup_to_driver_km) {
            phaseMap.navigating_to_pickup = ride.pickup_to_driver_km;
        } else {
            const dLat = ride.driver_initial_lat ?? ride.driver_lat;
            const dLng = ride.driver_initial_lng ?? ride.driver_lng;
            if (dLat && dLng && ride.pickup_lat && ride.pickup_lng) {
                phaseMap.navigating_to_pickup = haversine(dLat, dLng, ride.pickup_lat, ride.pickup_lng);
            }
        }
    }
    if (ride && !phaseMap.trip_in_progress) {
        phaseMap.trip_in_progress = ride.actual_distance_km || ride.distance_km || 0;
    }

    const meta = STATUS_META[ride?.status] ?? STATUS_META.searching;
    const StatusIcon = meta.icon;

    return (
        <>
            <Dialog open={open} onOpenChange={v => !v && onClose()}>
                <DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-y-auto p-0 gap-0" showCloseButton={true}>
                    <DialogTitle className="sr-only">
                        {ride ? `Ride ${ride.ride_code || ride.id}` : "Ride Details"}
                    </DialogTitle>

                    {loadError ? (
                        <div className="flex flex-col items-center justify-center py-24">
                            <p className="text-sm text-destructive">{loadError}</p>
                        </div>
                    ) : loading || !ride ? (
                        <div className="flex flex-col items-center justify-center py-24">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            <p className="text-sm text-muted-foreground mt-3">Loading ride details...</p>
                        </div>
                    ) : (
                        <div className="flex flex-col">

                            {/* ── Hero Header ──────────────────────────────── */}
                            <div className={`relative bg-gradient-to-r ${meta.gradient} px-6 py-5 text-white overflow-hidden`}>
                                {/* background pattern */}
                                <div className="absolute inset-0 opacity-10" style={{ backgroundImage: "radial-gradient(circle at 80% 50%, white 0%, transparent 60%)" }} />

                                <div className="relative flex items-start justify-between gap-4">
                                    <div className="flex items-center gap-4">
                                        <div className={`w-12 h-12 rounded-2xl bg-white/20 backdrop-blur-sm flex items-center justify-center ring-2 ${meta.ring} ring-offset-0 shrink-0`}>
                                            <StatusIcon className="h-6 w-6 text-white" />
                                        </div>
                                        <div>
                                            <p className="text-white/70 text-xs font-medium mb-0.5">
                                                {ride.ride_code ? `Ride #${ride.ride_code}` : "Ride Detail"}
                                            </p>
                                            <p className="text-2xl font-extrabold tracking-tight leading-none">{meta.label}</p>
                                            {ride.created_at && (
                                                <p className="text-white/60 text-xs mt-1">
                                                    {new Date(ride.created_at).toLocaleString("en-CA", { dateStyle: "medium", timeStyle: "short" })}
                                                </p>
                                            )}
                                        </div>
                                    </div>

                                    {isRideLive(ride.status) && (
                                        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end mr-8">
                                            <a href={`/dashboard/rides/live/${ride.id}`}
                                                className="flex items-center gap-1.5 text-xs font-semibold bg-white/20 hover:bg-white/30 text-white px-3 py-1.5 rounded-lg transition-colors backdrop-blur-sm">
                                                <Radio className="h-3 w-3 animate-pulse" /> Live Track
                                            </a>
                                        </div>
                                    )}
                                </div>

                                {/* UUID footer */}
                                <p className="relative text-[10px] font-mono text-white/40 mt-3 truncate" title={ride.id}>
                                    {ride.id}
                                </p>
                            </div>

                            {/* ── Body ─────────────────────────────────────── */}
                            <div className="p-5 space-y-4 bg-muted/20">

                                {/* Row 1: Route + Stats */}
                                <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">

                                    {/* Route card — 2/3 width */}
                                    <Card className="lg:col-span-2">
                                        <CardHead title="Route" icon={MapPin} />
                                        <div className="p-4">
                                            <div className="flex gap-3">
                                                <div className="flex flex-col items-center pt-1.5 shrink-0">
                                                    <div className="w-2.5 h-2.5 rounded-full bg-blue-500 ring-2 ring-blue-200 dark:ring-blue-900/50" />
                                                    <div className="w-0.5 flex-1 bg-gradient-to-b from-blue-400 to-red-400 my-1.5" style={{ minHeight: 32 }} />
                                                    <div className="w-2.5 h-2.5 rounded-full bg-red-500 ring-2 ring-red-200 dark:ring-red-900/50" />
                                                </div>
                                                <div className="flex-1 min-w-0 space-y-3">
                                                    <div>
                                                        <p className="text-[10px] font-bold uppercase tracking-wider text-blue-500 mb-0.5">Pickup</p>
                                                        <p className="text-sm font-medium">{ride.pickup_address || "—"}</p>
                                                    </div>
                                                    <div>
                                                        <p className="text-[10px] font-bold uppercase tracking-wider text-red-500 mb-0.5">Dropoff</p>
                                                        <p className="text-sm font-medium">{ride.dropoff_address || "—"}</p>
                                                    </div>
                                                </div>
                                            </div>
                                            {ride.rider_notes && (
                                                <div className="mt-3 pt-3 border-t flex items-start gap-2">
                                                    <MessageSquare className="h-3.5 w-3.5 text-muted-foreground mt-0.5 shrink-0" />
                                                    <div>
                                                        <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-0.5">Rider note / meeting instructions</p>
                                                        <p className="text-sm">{ride.rider_notes}</p>
                                                    </div>
                                                </div>
                                            )}
                                        </div>
                                    </Card>

                                    {/* Stats — 1/3 width */}
                                    <Card>
                                        <CardHead title="Trip Stats" icon={Gauge} />
                                        <div className="p-3 space-y-2">
                                            {/* Actual GPS vs Planned estimate */}
                                            {(() => {
                                                const actualKm = ride.actual_distance_km ?? phaseMap.trip_in_progress ?? null;
                                                const plannedKm = ride.planned_distance_km ?? null;
                                                    const actualSecs = ride.phase_durations?.trip_in_progress ?? null;
                                                    const actualMin = ride.actual_duration_minutes ?? (actualSecs != null ? Math.round(actualSecs / 60) : null);
                                                const plannedMin = ride.duration_minutes ?? null;
                                                const hasActual = actualKm != null || actualMin != null;
                                                return (
                                                    <>
                                                        {hasActual && (
                                                            <div className="rounded-lg bg-emerald-50 dark:bg-emerald-900/20 border border-emerald-100 dark:border-emerald-900/30 px-3 py-2">
                                                                <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400 mb-1.5">Actual (GPS)</p>
                                                                <div className="flex gap-4">
                                                                    <div className="flex items-center gap-1.5">
                                                                        <Route className="h-3.5 w-3.5 text-emerald-500" />
                                                                        <span className="text-sm font-extrabold tabular-nums text-emerald-700 dark:text-emerald-300">
                                                                            {actualKm != null ? `${Number(actualKm).toFixed(1)} km` : "—"}
                                                                        </span>
                                                                    </div>
                                                                    <div className="flex items-center gap-1.5">
                                                                        <Clock className="h-3.5 w-3.5 text-emerald-500" />
                                                                        <span className="text-sm font-extrabold tabular-nums text-emerald-700 dark:text-emerald-300">
                                                                            {actualMin != null ? `${actualMin} min` : "—"}
                                                                        </span>
                                                                    </div>
                                                                </div>
                                                            </div>
                                                        )}
                                                        <div className="rounded-lg bg-muted/50 border border-border px-3 py-2">
                                                            <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground mb-1.5">Estimated (Booking)</p>
                                                            <div className="flex gap-4">
                                                                <div className="flex items-center gap-1.5">
                                                                    <Route className="h-3.5 w-3.5 text-muted-foreground" />
                                                                    <span className="text-sm font-bold tabular-nums text-foreground">
                                                                        {plannedKm != null ? `${Number(plannedKm).toFixed(1)} km` : `${(ride.distance_km || 0).toFixed(1)} km`}
                                                                    </span>
                                                                </div>
                                                                <div className="flex items-center gap-1.5">
                                                                    <Clock className="h-3.5 w-3.5 text-muted-foreground" />
                                                                    <span className="text-sm font-bold tabular-nums text-foreground">
                                                                        {plannedMin != null ? `${plannedMin} min` : "—"}
                                                                    </span>
                                                                </div>
                                                            </div>
                                                        </div>
                                                    </>
                                                );
                                            })()}
                                            <div className="flex gap-2">
                                                <StatPill label="Surge" value={`${ride.surge_multiplier || 1.0}×`} icon={Percent} color="bg-amber-50 text-amber-700 dark:bg-amber-900/30 dark:text-amber-300" />
                                                <StatPill label="Total Fare" value={formatCurrency(ride.total_fare || 0)} icon={DollarSign} color="bg-emerald-50 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-300" />
                                            </div>
                                        </div>
                                    </Card>
                                </div>

                                {/* Row 2: Rider + Driver */}
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">

                                    {/* Rider Card */}
                                    <Card>
                                        <CardHead title="Rider" icon={User} accent="bg-blue-50/80 dark:bg-blue-900/20 border-blue-100 dark:border-blue-900/30" />
                                        <div className="p-4">
                                            <div className="flex items-center gap-3">
                                                <div className="w-11 h-11 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0 ring-2 ring-blue-200/50 dark:ring-blue-800/30">
                                                    {ride.rider_profile_image
                                                        ? <img src={ride.rider_profile_image} alt="" className="w-11 h-11 rounded-full object-cover" />
                                                        : <User className="h-5 w-5 text-blue-600 dark:text-blue-400" />}
                                                </div>
                                                <div className="flex-1 min-w-0">
                                                    <p className="text-sm font-semibold">{ride.rider_name || ride.rider_id?.slice(0, 12) || "—"}</p>
                                                    <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                                        {ride.rider_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{ride.rider_phone}</span>}
                                                        {ride.rider_email && <span className="flex items-center gap-1"><Mail className="h-3 w-3" />{ride.rider_email}</span>}
                                                    </div>
                                                </div>
                                                {ride.rider_flag_count > 0 && (
                                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md shrink-0 ${ride.rider_flag_count >= 2 ? "bg-destructive/15 text-destructive" : "bg-warning/15 text-warning"}`}>
                                                        {ride.rider_flag_count} flag{ride.rider_flag_count > 1 ? "s" : ""}
                                                    </span>
                                                )}
                                            </div>
                                            <div className="grid grid-cols-3 gap-2 mt-4">
                                                {[
                                                    { icon: Hash, value: ride.rider_total_rides ?? "—", label: "Rides" },
                                                    { icon: MapPinned, value: ride.rider_region || ride.rider_city || "—", label: "Region" },
                                                    { icon: CalendarDays, value: ride.rider_joined ? new Date(ride.rider_joined).toLocaleDateString("en-CA", { month: "short", year: "numeric" }) : "—", label: "Since" },
                                                ].map(({ icon: Icon, value, label }) => (
                                                    <div key={label} className="bg-muted/40 rounded-lg p-2.5 text-center">
                                                        <Icon className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                        <p className="text-xs font-bold truncate">{value}</p>
                                                        <p className="text-[9px] text-muted-foreground">{label}</p>
                                                    </div>
                                                ))}
                                            </div>
                                        </div>
                                    </Card>

                                    {/* Driver Card */}
                                    <Card>
                                        <CardHead title="Driver" icon={Car} accent="bg-emerald-50/80 dark:bg-emerald-900/20 border-emerald-100 dark:border-emerald-900/30" />
                                        <div className="p-4">
                                            {ride.driver_id ? (
                                                <>
                                                    <div className="flex items-center gap-3">
                                                        <div className="w-11 h-11 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center shrink-0 ring-2 ring-emerald-200/50 dark:ring-emerald-800/30">
                                                            {ride.driver_photo_url
                                                                ? <img src={ride.driver_photo_url} alt="" className="w-11 h-11 rounded-full object-cover" />
                                                                : <Car className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />}
                                                        </div>
                                                        <div className="flex-1 min-w-0">
                                                            <p className="text-sm font-semibold">{ride.driver_name || ride.driver_id?.slice(0, 12)}</p>
                                                            <div className="flex items-center gap-2 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                                                {ride.driver_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{ride.driver_phone}</span>}
                                                                {ride.driver_license_plate && <span className="font-mono font-bold text-foreground bg-muted px-1.5 py-0.5 rounded text-[11px]">{ride.driver_license_plate}</span>}
                                                            </div>
                                                        </div>
                                                        <div className="flex items-center gap-1.5 shrink-0">
                                                            {ride.driver_flag_count > 0 && (
                                                                <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${ride.driver_flag_count >= 2 ? "bg-destructive/15 text-destructive" : "bg-warning/15 text-warning"}`}>
                                                                    {ride.driver_flag_count} flag{ride.driver_flag_count > 1 ? "s" : ""}
                                                                </span>
                                                            )}
                                                            {ride.driver_rating != null && (
                                                                <span className="flex items-center gap-0.5 bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 rounded-md">
                                                                    <Star className="h-3 w-3 text-amber-400 fill-amber-400" />
                                                                    <span className="text-xs font-bold">{Number(ride.driver_rating).toFixed(1)}</span>
                                                                </span>
                                                            )}
                                                        </div>
                                                    </div>

                                                    {/* Vehicle */}
                                                    <div className="mt-3 pt-3 border-t space-y-1.5 text-xs text-muted-foreground">
                                                        {[ride.driver_vehicle_color, ride.driver_vehicle_year, ride.driver_vehicle_make, ride.driver_vehicle_model].filter(Boolean).length > 0 && (
                                                            <div className="flex items-center gap-2">
                                                                <Car className="h-3.5 w-3.5 shrink-0" />
                                                                <span className="font-medium text-foreground">{[ride.driver_vehicle_color, ride.driver_vehicle_year, ride.driver_vehicle_make, ride.driver_vehicle_model].filter(Boolean).join(" ")}</span>
                                                            </div>
                                                        )}
                                                        {ride.driver_vehicle_type_name && (
                                                            <div className="flex items-center gap-2">
                                                                <Users className="h-3.5 w-3.5 shrink-0" />
                                                                <span>{ride.driver_vehicle_type_name}</span>
                                                                {ride.driver_vehicle_capacity > 0 && <span>({ride.driver_vehicle_capacity} seats)</span>}
                                                            </div>
                                                        )}
                                                        {ride.driver_vehicle_vin && (
                                                            <div className="flex items-center gap-2">
                                                                <Shield className="h-3.5 w-3.5 shrink-0" />
                                                                <span className="font-mono">VIN: {ride.driver_vehicle_vin}</span>
                                                            </div>
                                                        )}
                                                    </div>

                                                    {/* Driver stats */}
                                                    <div className="grid grid-cols-4 gap-2 mt-3">
                                                        {[
                                                            { icon: Hash, value: ride.driver_total_rides ?? ride.driver_completed_rides ?? "—", label: "Rides" },
                                                            { icon: Gauge, value: ride.driver_acceptance_rate != null ? `${ride.driver_acceptance_rate}%` : "—", label: "Compl." },
                                                            { icon: Star, value: ride.driver_rating ? Number(ride.driver_rating).toFixed(1) : "—", label: "Rating" },
                                                            { icon: MapPinned, value: ride.driver_region || ride.driver_city || "—", label: "Region" },
                                                        ].map(({ icon: Icon, value, label }) => (
                                                            <div key={label} className="bg-muted/40 rounded-lg p-2.5 text-center">
                                                                <Icon className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                                <p className="text-xs font-bold truncate">{value}</p>
                                                                <p className="text-[9px] text-muted-foreground">{label}</p>
                                                            </div>
                                                        ))}
                                                    </div>
                                                </>
                                            ) : (
                                                <div className="flex flex-col items-center py-8 text-muted-foreground">
                                                    <Car className="h-8 w-8 opacity-20 mb-2" />
                                                    <p className="text-sm">No driver assigned</p>
                                                </div>
                                            )}
                                        </div>
                                    </Card>
                                </div>

                                {/* Row 3: Fare Breakdown + Revenue Split */}
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                    <Card>
                                        <CardHead title="Fare Breakdown" icon={Receipt} />
                                        {(() => {
                                            const n = (v: any) => parseFloat(String(v ?? 0)) || 0;
                                            const areaFees = Array.isArray(ride.area_fees_breakdown) ? ride.area_fees_breakdown : [];
                                            const hasAreaFees = areaFees.some((f: any) => n(f.calculated_value) > 0);
                                            const areaFeesTotal = n(ride.area_fees_total) || areaFees.reduce((s: number, f: any) => s + n(f.calculated_value), 0);
                                            // tax_breakdown: { "GST": {rate, amount}, "PST": {rate, amount} }
                                            const taxEntries: [string, number, number | null][] = ride.tax_breakdown && typeof ride.tax_breakdown === "object"
                                                ? Object.entries(ride.tax_breakdown).map(([k, t]: any) => [
                                                    k,
                                                    n(t?.amount ?? t),
                                                    (t && typeof t === "object" && t.rate != null) ? Number(t.rate) : null,
                                                ])
                                                : [];
                                            const taxTotal = ride.tax_amount != null ? n(ride.tax_amount) : taxEntries.reduce((s, [, a]) => s + a, 0);
                                            const discount = n(ride.discount_amount);
                                            const tip = n(ride.tip_amount);
                                            const grand = ride.grand_total != null
                                                ? n(ride.grand_total)
                                                : n(ride.total_fare) + n(ride.area_fees_total) + taxTotal - discount;
                                            return (
                                                <div className="p-4 space-y-1">
                                                    <FR l="Base fare" v={ride.base_fare} />
                                                    <FR l={`Distance (${(ride.distance_km || 0).toFixed(1)} km)`} v={ride.distance_fare} />
                                                    <FR l={`Time (${ride.duration_minutes || 0} min)`} v={ride.time_fare} />
                                                    {(ride.surge_multiplier || 1) > 1 && (
                                                        <p className="text-[11px] text-amber-600 dark:text-amber-400">Includes {ride.surge_multiplier}× surge</p>
                                                    )}
                                                    <FR l="Booking fee" v={ride.booking_fee} />
                                                    {/* airport_fee is charged/persisted separately from dynamic
                                                     *  area fees, so always render it when present. */}
                                                    {n(ride.airport_fee) > 0 && <FR l="Airport fee" v={ride.airport_fee} />}
                                                    {hasAreaFees && areaFees.map((f: any, i: number) => (
                                                        n(f.calculated_value) > 0 ? <FR key={i} l={f.name || "Area fee"} v={n(f.calculated_value)} /> : null
                                                    ))}
                                                    <div className="border-t my-2" />
                                                    <FR l="Subtotal (pre-tax)" v={n(ride.total_fare) + areaFeesTotal} b />
                                                    {discount > 0 && (
                                                        <div className="flex justify-between items-center">
                                                            <span className="flex items-center gap-2 text-sm"><Ticket className="h-4 w-4 text-violet-500" />Promo{ride.promo_code ? <b className="font-mono text-xs bg-violet-50 dark:bg-violet-900/20 px-1.5 py-0.5 rounded">{ride.promo_code}</b> : null}</span>
                                                            <span className="text-sm font-semibold text-emerald-600">-{formatCurrency(discount)}</span>
                                                        </div>
                                                    )}
                                                    {taxEntries.length > 0
                                                        ? taxEntries.map(([name, amt, rate]) => (
                                                            <FR key={name} l={`${name}${rate != null ? ` (${rate}%)` : ""}`} v={amt} />
                                                        ))
                                                        : taxTotal > 0 && <FR l="Tax" v={taxTotal} />}
                                                    {tip > 0 && (
                                                        <div className="flex justify-between items-center">
                                                            <span className="flex items-center gap-2 text-sm"><DollarSign className="h-4 w-4 text-amber-500" />Tip</span>
                                                            <span className="text-sm font-semibold text-amber-600">{formatCurrency(tip)}</span>
                                                        </div>
                                                    )}
                                                    <div className="border-t my-2" />
                                                    <FR l="Grand total" v={grand} b />
                                                    {tip > 0 && <FR l="Total charged (incl. tip)" v={grand + tip} b />}
                                                </div>
                                            );
                                        })()}
                                    </Card>

                                    <Card>
                                        <CardHead title="Driver & Platform Earnings" icon={TrendingUp} />
                                        {(() => {
                                            const n = (v: any) => parseFloat(String(v ?? 0)) || 0;
                                            const isCancelled = ride.status === "cancelled";
                                            // 0% commission: driver keeps 100% of the ride fare components — but a
                                            // CANCELLED ride never ran, so the driver earns only the cancellation
                                            // fee (the estimated fare components on the row are not earned).
                                            const rideFare = isCancelled ? 0 : n(ride.base_fare) + n(ride.distance_fare) + n(ride.time_fare);
                                            const tip = n(ride.tip_amount);
                                            const incentives = n(ride.incentive_total);
                                            const claims = Array.isArray(ride.incentive_claims) ? ride.incentive_claims : [];
                                            const cancelFee = n(ride.cancellation_fee_driver); // driver income on cancellations
                                            const driverTotal = rideFare + tip + incentives + cancelFee;
                                            // Platform revenue = booking + airport (admin_earnings) PLUS area fees
                                            // (Platform/Insurance/City/Infra) — these are collected in grand_total
                                            // but never written to admin_earnings, so add them here.
                                            const areaFeesArr = Array.isArray(ride.area_fees_breakdown) ? ride.area_fees_breakdown : [];
                                            const areaFeesTotal = n(ride.area_fees_total) || areaFeesArr.reduce((s: number, f: any) => s + n(f.calculated_value), 0);
                                            const bookingAirport = n(ride.admin_earnings); // booking + airport
                                            const platformGross = bookingAirport + areaFeesTotal;
                                            const discount = n(ride.discount_amount);
                                            // GST/PST are pass-through: collected from the rider, remitted by the
                                            // driver (the GST registrant) — NOT platform income.
                                            const taxTotal = n(ride.tax_amount);
                                            // Platform funds incentives + absorbs promo discount (0% commission model).
                                            const platformNet = platformGross - incentives - discount;
                                            return (
                                                <div className="p-4">
                                                    <div className="grid grid-cols-3 gap-2.5 mb-4">
                                                        <div className="bg-emerald-50 dark:bg-emerald-900/20 rounded-xl p-3.5 text-center">
                                                            <p className="text-lg font-extrabold text-emerald-600 dark:text-emerald-400">{formatCurrency(driverTotal)}</p>
                                                            <p className="text-[11px] text-muted-foreground mt-1 font-medium">Driver receives</p>
                                                        </div>
                                                        <div className="bg-violet-50 dark:bg-violet-900/20 rounded-xl p-3.5 text-center">
                                                            <p className="text-lg font-extrabold text-violet-600 dark:text-violet-400">{formatCurrency(platformNet)}</p>
                                                            <p className="text-[11px] text-muted-foreground mt-1 font-medium">Platform net</p>
                                                        </div>
                                                        <div className="bg-amber-50 dark:bg-amber-900/20 rounded-xl p-3.5 text-center">
                                                            <p className="text-lg font-extrabold text-amber-600 dark:text-amber-400">{formatCurrency(incentives)}</p>
                                                            <p className="text-[11px] text-muted-foreground mt-1 font-medium">Incentives</p>
                                                        </div>
                                                    </div>
                                                    <div className="border-t pt-3 space-y-1.5">
                                                        <p className="text-[10px] font-bold uppercase tracking-wider text-emerald-600 dark:text-emerald-400">Driver</p>
                                                        {!isCancelled && <FR l="Ride fare (100%)" v={rideFare} />}
                                                        {cancelFee > 0 && <FR l="Cancellation fee" v={cancelFee} />}
                                                        {tip > 0 && <FR l="Tip" v={tip} />}
                                                        {claims.length > 0
                                                            ? claims.map((c: any, i: number) => (
                                                                <FR key={i} l={`Incentive — ${c.name || "Bonus"}`} v={n(c.bonus_amount)} />
                                                            ))
                                                            : incentives > 0 && <FR l="Incentives" v={incentives} />}
                                                        <FR l="Driver total" v={driverTotal} b />
                                                        <div className="border-t my-2" />
                                                        <p className="text-[10px] font-bold uppercase tracking-wider text-violet-600 dark:text-violet-400">Platform</p>
                                                        <FR l="Booking + airport fees" v={bookingAirport} />
                                                        {areaFeesTotal > 0 && <FR l="Area fees (Platform/Insurance/City/Infra)" v={areaFeesTotal} />}
                                                        {incentives > 0 && <FR l="Less incentives funded" v={-incentives} />}
                                                        {discount > 0 && <FR l="Less promo absorbed" v={-discount} />}
                                                        <FR l="Platform net" v={platformNet} b />
                                                        {taxTotal > 0 && (
                                                            <>
                                                                <div className="border-t my-2" />
                                                                <p className="text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Tax (pass-through)</p>
                                                                <FR l="GST/PST collected" v={taxTotal} />
                                                                <p className="text-[10px] text-muted-foreground">Collected from rider · remitted by driver — not platform income.</p>
                                                            </>
                                                        )}
                                                    </div>
                                                </div>
                                            );
                                        })()}
                                    </Card>
                                </div>

                                {/* Row 4: Payment + Rating */}
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                                    <Card>
                                        <CardHead title="Payment" icon={CreditCard} />
                                        <div className="p-4 flex items-center justify-between">
                                            <span className="flex items-center gap-2 text-sm font-medium">
                                                <Receipt className="h-4 w-4 text-muted-foreground" />
                                                {ride.payment_method || "Card"}
                                                {ride.card_last4 && (
                                                    <span className="text-muted-foreground font-normal">
                                                        · {ride.card_brand || "Card"} •••• {ride.card_last4}
                                                    </span>
                                                )}
                                            </span>
                                            <span className={`text-xs font-bold px-2.5 py-1 rounded-lg ${
                                                ride.payment_status === "paid" || ride.payment_status === "waived_admin"
                                                    ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400"
                                                    : ride.payment_status === "failed"
                                                        ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400"
                                                        : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"
                                            }`}>
                                                {(ride.payment_status || "pending").replace(/_/g, " ").toUpperCase()}
                                            </span>
                                        </div>
                                        {ride.status === "completed" && (
                                            <div className="px-4 pb-4 -mt-1">
                                                <div className="flex items-center justify-between gap-2 pt-3 border-t">
                                                    <span className="text-[11px] font-medium text-muted-foreground">Receipt</span>
                                                    <RideInvoice rideId={ride.id} status={ride.status} paymentStatus={ride.payment_status} />
                                                </div>
                                            </div>
                                        )}
                                    </Card>

                                    <Card>
                                        <CardHead title="Rating" icon={Star} />
                                        <div className="p-4">
                                            {ride.rider_rating ? (
                                                <div>
                                                    <div className="flex items-center gap-1 mb-2">
                                                        {[1, 2, 3, 4, 5].map(i => (
                                                            <Star key={i} className={`h-5 w-5 ${i <= ride.rider_rating ? "text-amber-400 fill-amber-400" : "text-muted/30"}`} />
                                                        ))}
                                                        <span className="text-sm font-bold ml-1.5">{ride.rider_rating}/5</span>
                                                    </div>
                                                    {ride.rider_comment && (
                                                        <p className="text-sm text-muted-foreground italic bg-muted/40 rounded-lg px-3 py-2">&quot;{ride.rider_comment}&quot;</p>
                                                    )}
                                                </div>
                                            ) : (
                                                <p className="text-sm text-muted-foreground py-1">No rating yet</p>
                                            )}
                                        </div>
                                    </Card>
                                </div>

                                {/* Cancellation */}
                                {ride.status === "cancelled" && (
                                    <Card className="border-destructive/30">
                                        <CardHead title="Cancellation" icon={XCircle} accent="bg-destructive/10 border-destructive/20" />
                                        <div className="p-4 space-y-1">
                                            <FR l="Cancelled by" v={ride.cancelled_by ? String(ride.cancelled_by).replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "—"} t />
                                            <FR l="Type" v={ride.cancellation_type === "no_drivers_found" ? "No drivers found" : ride.cancellation_type ? String(ride.cancellation_type).replace(/_/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()) : "—"} t />
                                            <FR l="Reason" v={ride.cancellation_reason || "—"} t />
                                            <FR l="Driver fee" v={ride.cancellation_fee_driver} />
                                            <FR l="Admin fee" v={ride.cancellation_fee_admin} />
                                        </div>
                                    </Card>
                                )}

                                {/* Booking Logs + Route Replay */}
                                <Card>
                                    <CardHead title="Timeline & Route Replay" icon={Clock} />
                                    <div className="p-4 space-y-3">
                                        {/* Timeline */}
                                        <div className="space-y-0.5">
                                            <TL l="Requested" t={ride.ride_requested_at || ride.created_at} />
                                            <TL l="Driver notified" t={ride.driver_notified_at} />
                                            <TL l="Driver accepted" t={ride.driver_accepted_at} km={phaseMap.navigating_to_pickup} />
                                            <TL l="Driver arrived" t={ride.driver_arrived_at} km={phaseMap.arrived_at_pickup} />
                                            <TL l="Ride started" t={ride.ride_started_at} />
                                            <TL l="Ride completed" t={ride.ride_completed_at} km={phaseMap.trip_in_progress} />
                                            {ride.cancelled_at && <TL l="Cancelled" t={ride.cancelled_at} d />}
                                        </div>

                                        {/* Phase selector */}
                                        <div className="pt-3 border-t">
                                            <div className="flex items-center justify-between mb-2.5">
                                                <div className="flex items-center gap-1.5">
                                                    <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider">Route Views</p>
                                                    {isImported && (
                                                        <span className="text-[10px] font-medium text-muted-foreground bg-muted rounded px-1.5 py-0.5">
                                                            Imported
                                                        </span>
                                                    )}
                                                </div>
                                                <p className="text-[10px] text-muted-foreground/70">
                                                    {isImported ? "Planned route only — no GPS captured" : "Click to replay on map"}
                                                </p>
                                            </div>
                                            <div className="grid grid-cols-3 gap-2">
                                                {(() => {
                                                    const fmtDur = (s: number | undefined) => typeof s === "number" && s > 0 ? s >= 60 ? `${Math.round(s / 60)} min` : `${s}s` : null;
                                                    const fmtKm = (v: number | undefined | null) => typeof v === "number" ? `${Number(v).toFixed(2)} km` : "—";
                                                    type CardKey = "pickup" | "actual" | "planned";
                                                    const cards: { key: CardKey; label: string; km: string; sub: string | null; colorCls: string; }[] = [
                                                        { key: "pickup",  label: "Pickup",       km: fmtKm(ride.phase_distances?.navigating_to_pickup), sub: isImported ? "Not captured (imported)" : fmtDur(ride.phase_durations?.navigating_to_pickup), colorCls: PHASE_COLORS.navigating_to_pickup },
                                                        { key: "actual",  label: "Actual Trip",  km: fmtKm(ride.phase_distances?.trip_in_progress ?? ride.actual_distance_km), sub: isImported ? "Not captured (imported)" : Number(ride.route_schema_version || 0) >= 2 ? routeQualityLabel(ride.route_quality) : fmtDur(ride.phase_durations?.trip_in_progress), colorCls: PHASE_COLORS.trip_in_progress },
                                                        // Imported rides never got a planned_distance_km, but the OSRM
                                                        // backfill wrote the road distance to distance_km — show that
                                                        // rather than an em-dash beside a route the map is drawing.
                                                        { key: "planned", label: "Planned Trip", km: fmtKm(ride.planned_distance_km ?? (isImported ? ride.distance_km : undefined)), sub: (Array.isArray(ride.planned_route_polyline) && ride.planned_route_polyline.length > 1) ? (isImported ? "Planned route (imported)" : "Planned route") : "No planned road geometry", colorCls: "bg-muted/60 text-foreground" },
                                                    ];
                                                    return cards.map(c => (
                                                        <button key={c.key} type="button" onClick={() => setSelectedPhase(c.key)}
                                                            className={`text-left rounded-xl px-3 py-2.5 transition ring-offset-1 ring-offset-background ${c.colorCls} hover:brightness-95 dark:hover:brightness-110 ${selectedPhase === c.key ? "ring-2 ring-primary" : "ring-0"}`}>
                                                            <p className="text-xs font-bold">{c.km}</p>
                                                            <p className="text-[10px] opacity-80">{c.label}</p>
                                                            <p className="text-[9px] opacity-60">{c.sub || "—"}</p>
                                                        </button>
                                                    ));
                                                })()}
                                            </div>
                                        </div>

                                        {/* Map */}
                                        {ride.pickup_lat && ride.dropoff_lat && (() => {
                                            const pp = ride.phase_polylines || {};
                                            const pickupPts = Array.isArray(pp.navigating_to_pickup)
                                                ? pp.navigating_to_pickup.map((p: any) => ({ lat: p[0], lng: p[1], timestamp: p[2] })) : [];
                                            const tripPts = Array.isArray(pp.trip_in_progress)
                                                ? pp.trip_in_progress.map((p: any) => ({ lat: p[0], lng: p[1], timestamp: p[2] })) : [];

                                            let pickupProp: typeof pickupPts | undefined;
                                            let pickupApprox = false;
                                            let tripProp: typeof tripPts | undefined;
                                            let trailForMap: typeof tripPts | undefined;
                                            let plannedProp: { lat: number; lng: number }[] | undefined;
                                            let actualSegmentsProp: unknown;
                                            let label = "";
                                            let emptyHint: string | null = null;

                                            // An imported ride has no GPS of any kind. Both GPS views must
                                            // say so and draw nothing — the straight-line fallback would
                                            // otherwise render a pickup→dropoff line indistinguishable
                                            // from a real trace on the exact screen used for SGI and
                                            // dispute review.
                                            const importedNoGps = isImported && selectedPhase !== "planned";

                                            if (importedNoGps) {
                                                label = selectedPhase === "pickup"
                                                    ? "Driver → Pickup (not captured)"
                                                    : "Pickup → Dropoff (not captured)";
                                                emptyHint = "Imported from the previous app — no GPS was recorded for this ride";
                                            } else if (selectedPhase === "pickup") {
                                                // Prefer the OSRM road-matched pickup leg, then raw Phase 2
                                                // GPS, then a driver-start → pickup reference line so the
                                                // card always shows the approach.
                                                const pickupRoad = Array.isArray(ride.road_polyline_pickup) && ride.road_polyline_pickup.length > 1
                                                    ? ride.road_polyline_pickup.map((p: any) => ({ lat: p[0], lng: p[1] })) : [];
                                                if (pickupRoad.length > 1) {
                                                    label = "Driver → Pickup (road-matched)";
                                                    pickupProp = pickupRoad;
                                                } else if (pickupPts.length > 1) {
                                                    label = "Driver → Pickup (actual GPS)";
                                                    pickupProp = pickupPts;
                                                } else {
                                                    // No GPS captured for the approach — draw a dashed
                                                    // reference from the driver's start to the pickup.
                                                    const dLat = ride.driver_initial_lat ?? ride.driver_lat;
                                                    const dLng = ride.driver_initial_lng ?? ride.driver_lng;
                                                    if (dLat != null && dLng != null && ride.pickup_lat != null && ride.pickup_lng != null) {
                                                        label = "Driver → Pickup (approx — no GPS captured)";
                                                        pickupProp = [{ lat: dLat, lng: dLng }, { lat: ride.pickup_lat, lng: ride.pickup_lng }];
                                                        pickupApprox = true;
                                                    } else {
                                                        label = "Driver → Pickup";
                                                        emptyHint = "No Phase 2 GPS trail for this ride";
                                                    }
                                                }
                                            } else if (selectedPhase === "actual") {
                                                const isV2Route = Number(ride.route_schema_version || 0) >= 2;
                                                if (isV2Route) {
                                                    actualSegmentsProp = ride.actual_route_segments;
                                                    const segmentCount = Array.isArray(ride.actual_route_segments)
                                                        ? ride.actual_route_segments.length : 0;
                                                    label = `Pickup → Dropoff (actual GPS) · ${routeQualityLabel(ride.route_quality)}`;
                                                    if (!segmentCount) {
                                                        emptyHint = "No captured GPS segments are available for this ride";
                                                    }
                                                } else {
                                                // Prefer the OSRM road-matched line (ride_routes.road_polyline) —
                                                // the clean on-road route SGI / dispute review should see — then
                                                // fall back to raw trip GPS, then the legacy combined polyline.
                                                const roadPts = Array.isArray(ride.road_polyline) && ride.road_polyline.length > 1
                                                    ? ride.road_polyline.map((p: any) => ({ lat: p[0], lng: p[1] })) : [];
                                                if (roadPts.length > 1) {
                                                    label = "Pickup → Dropoff (road-matched)";
                                                    tripProp = roadPts;
                                                } else if (tripPts.length > 1) {
                                                    label = "Pickup → Dropoff (actual GPS)";
                                                    tripProp = tripPts;
                                                } else {
                                                    label = "Pickup → Dropoff (actual GPS)";
                                                    const legacy = Array.isArray(ride.route_polyline) && ride.route_polyline.length > 1
                                                        ? ride.route_polyline.map((p: any) => ({ lat: p[0], lng: p[1] })) : [];
                                                    if (legacy.length > 1) trailForMap = legacy;
                                                    else emptyHint = "No trip GPS trail for this ride";
                                                }
                                                }
                                            } else {
                                                // Planned route: the road-following polyline captured from
                                                // the Directions API at booking. Falls back to the dashed
                                                // straight line (drawn by the map) when none was stored.
                                                const plannedPts = Array.isArray(ride.planned_route_polyline) && ride.planned_route_polyline.length > 1
                                                    ? ride.planned_route_polyline.map((p: any) => ({ lat: p[0], lng: p[1] })) : [];
                                                if (plannedPts.length > 1) {
                                                    label = "Planned Trip (road-following)";
                                                    plannedProp = plannedPts;
                                                } else {
                                                    label = "Planned Trip (straight-line reference)";
                                                }
                                            }

                                            return (
                                                <div className="pt-3 border-t">
                                                    <div className="flex items-center justify-between mb-2">
                                                        <p className="text-xs text-muted-foreground">{label}</p>
                                                        {emptyHint && <p className="text-[10px] text-muted-foreground/70">{emptyHint}</p>}
                                                    </div>
                                                    <div className="rounded-xl overflow-hidden">
                                                        <RideRouteMap
                                                            key={selectedPhase}
                                                            pickupLat={ride.pickup_lat}
                                                            pickupLng={ride.pickup_lng}
                                                            dropoffLat={ride.dropoff_lat}
                                                            dropoffLng={ride.dropoff_lng}
                                                            pickupTrail={pickupProp}
                                                            pickupApprox={pickupApprox}
                                                            tripTrail={tripProp}
                                                            plannedTrail={plannedProp}
                                                            locationTrail={trailForMap}
                                                            actualSegments={actualSegmentsProp}
                                                            suppressStraightFallback={importedNoGps}
                                                        />
                                                    </div>
                                                </div>
                                            );
                                        })()}
                                    </div>
                                </Card>

                                {/* Offer Funnel — who was offered this ride and what they did.
                                 *  Always rendered so the section is discoverable; shows an empty
                                 *  state for rides with no recorded offers (e.g. admin-created or
                                 *  pre-batch-dispatch rides). */}
                                {(() => {
                                    const offers: any[] = Array.isArray(ride.offers) ? ride.offers : [];
                                    return (
                                    <Card>
                                        <CardHead title={`Dispatch Offers · ${offers.length} driver${offers.length === 1 ? "" : "s"}`} icon={Radio} />
                                        <div className="p-4 space-y-2">
                                            {offers.length === 0 ? (
                                                <p className="text-sm text-muted-foreground">No dispatch offers recorded for this ride. (Offers are logged for rides matched through the batch-dispatch engine.)</p>
                                            ) : (() => {
                                                const OFFER_META: Record<string, { label: string; cls: string; Icon: React.ElementType }> = {
                                                    accepted:  { label: "Accepted",  cls: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400", Icon: CheckCircle2 },
                                                    declined:  { label: "Declined",  cls: "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400", Icon: XCircle },
                                                    expired:   { label: "Ignored",   cls: "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400", Icon: Clock },
                                                    preempted: { label: "Preempted", cls: "bg-blue-100 text-blue-700 dark:bg-blue-900/30 dark:text-blue-400", Icon: Radio },
                                                    pending:   { label: "Pending",   cls: "bg-muted text-muted-foreground", Icon: Radio },
                                                };
                                                const respSecs = (o: any) => {
                                                    if (!o.offered_at || !o.responded_at) return null;
                                                    const d = (new Date(o.responded_at).getTime() - new Date(o.offered_at).getTime()) / 1000;
                                                    return Number.isFinite(d) && d >= 0 ? d : null;
                                                };
                                                const counts = offers.reduce((acc: Record<string, number>, o: any) => {
                                                    const k = o.status === "expired" ? "ignored" : (o.status || "pending");
                                                    acc[k] = (acc[k] || 0) + 1; return acc;
                                                }, {});
                                                const summary = [
                                                    { k: "received", lbl: "Received", v: offers.length, cls: "text-foreground" },
                                                    { k: "accepted", lbl: "Accepted", v: counts.accepted || 0, cls: "text-emerald-600 dark:text-emerald-400" },
                                                    { k: "declined", lbl: "Rejected", v: counts.declined || 0, cls: "text-red-600 dark:text-red-400" },
                                                    { k: "ignored", lbl: "Ignored", v: counts.ignored || 0, cls: "text-amber-600 dark:text-amber-400" },
                                                    { k: "preempted", lbl: "Preempted", v: counts.preempted || 0, cls: "text-blue-600 dark:text-blue-400" },
                                                ];
                                                return (
                                                    <>
                                                        <div className="grid grid-cols-5 gap-2 mb-2">
                                                            {summary.map((s) => (
                                                                <div key={s.k} className="rounded-lg bg-muted/50 px-2 py-2 text-center">
                                                                    <p className={`text-lg font-extrabold tabular-nums leading-none ${s.cls}`}>{s.v}</p>
                                                                    <p className="text-[10px] text-muted-foreground mt-1">{s.lbl}</p>
                                                                </div>
                                                            ))}
                                                        </div>
                                                        {offers.map((o: any, i: number) => {
                                                            const meta = OFFER_META[o.status] || OFFER_META.pending;
                                                            const rs = respSecs(o);
                                                            return (
                                                                <div key={`${o.driver_id}-${i}`} className="bg-muted/40 rounded-lg p-2.5">
                                                                    <div className="flex items-center gap-2.5">
                                                                        <meta.Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                                                        <span className="text-xs font-semibold truncate flex-1">{o.driver_name || o.driver_id?.slice(0, 12)}</span>
                                                                        {o.driver_rating != null && (
                                                                            <span className="flex items-center gap-0.5 text-[11px] text-muted-foreground">
                                                                                <Star className="h-3 w-3 text-amber-400 fill-amber-400" />{Number(o.driver_rating).toFixed(1)}
                                                                            </span>
                                                                        )}
                                                                        {typeof o.eta_seconds === "number" && (
                                                                            <span className="text-[11px] text-muted-foreground tabular-nums">{Math.round(o.eta_seconds)}s ETA</span>
                                                                        )}
                                                                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${meta.cls}`}>{meta.label}</span>
                                                                    </div>
                                                                    <div className="flex items-center flex-wrap gap-x-3 gap-y-0.5 mt-1 pl-6 text-[10px] text-muted-foreground tabular-nums">
                                                                        <span>Offered {fmtTime(o.offered_at)}</span>
                                                                        {o.responded_at && (
                                                                            <span>
                                                                                {o.status === "accepted" ? "Accepted" : o.status === "declined" ? "Declined" : o.status === "preempted" ? "Preempted (another driver took it)" : "Closed"} {fmtTime(o.responded_at)}
                                                                                {rs != null && ` (${rs < 60 ? `${Math.round(rs)}s` : `${Math.round(rs / 60)}m`})`}
                                                                            </span>
                                                                        )}
                                                                        {!o.responded_at && o.status === "expired" && <span>Timed out (no response)</span>}
                                                                    </div>
                                                                </div>
                                                            );
                                                        })}
                                                    </>
                                                );
                                            })()}
                                        </div>
                                    </Card>
                                    );
                                })()}

                                {/* Lost & Found */}
                                <Card>
                                    <div className="p-4">
                                        <RideLostFound rideId={ride.id} items={ride.lost_and_found || []} onRefresh={loadRide} />
                                    </div>
                                </Card>

                                {/* Complaints & Flags */}
                                <Card>
                                    <CardHead title="Complaints & Flags" icon={Flag} />
                                    <div className="p-4 space-y-3">
                                        {ride.flags && ride.flags.length > 0 && (
                                            <div className="space-y-2">
                                                {ride.flags.map((f: any, i: number) => (
                                                    <div key={f.id || i} className="flex items-center gap-2.5 bg-muted/40 rounded-lg p-2.5">
                                                        <Flag className={`h-3.5 w-3.5 shrink-0 ${f._party === "rider" ? "text-blue-500" : "text-emerald-500"}`} />
                                                        <span className="text-xs font-semibold">{f._party === "rider" ? "Rider" : "Driver"}</span>
                                                        <span className="text-xs bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 px-1.5 py-0.5 rounded font-medium">{f.reason?.replace(/_/g, " ")}</span>
                                                        {f.description && <span className="text-xs text-muted-foreground truncate flex-1">{f.description}</span>}
                                                        <span className="text-[10px] text-muted-foreground tabular-nums">{fmtTime(f.created_at)}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {ride.complaints && ride.complaints.length > 0 && (
                                            <div className="space-y-2">
                                                {ride.complaints.map((c: any) => (
                                                    <div key={c.id} className="flex items-center gap-2.5 bg-muted/40 rounded-lg p-2.5">
                                                        <FileWarning className="h-3.5 w-3.5 shrink-0 text-warning" />
                                                        <span className="text-xs font-semibold">{c.against_type}</span>
                                                        <span className="text-xs bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 px-1.5 py-0.5 rounded font-medium">{c.category}</span>
                                                        <span className="text-xs text-muted-foreground truncate flex-1">{c.description}</span>
                                                        <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${c.status === "open" ? "bg-warning/15 text-warning" : "bg-success/15 text-success"}`}>{c.status}</span>
                                                    </div>
                                                ))}
                                            </div>
                                        )}

                                        {!ride.flags?.length && !ride.complaints?.length && (
                                            <p className="text-sm text-muted-foreground">No flags or complaints on this ride.</p>
                                        )}

                                        {(ride.rider_flag_count >= 2 || ride.driver_flag_count >= 2) && (
                                            <div className="flex items-center gap-2.5 bg-destructive/10 rounded-lg p-3">
                                                <AlertTriangle className="h-4 w-4 text-destructive shrink-0" />
                                                <p className="text-xs font-semibold text-destructive">
                                                    {ride.rider_flag_count >= 2 && `Rider has ${ride.rider_flag_count} flags (1 more = ban). `}
                                                    {ride.driver_flag_count >= 2 && `Driver has ${ride.driver_flag_count} flags (1 more = ban).`}
                                                </p>
                                            </div>
                                        )}

                                        <div className="flex gap-2 flex-wrap pt-1 border-t">
                                            {ride.rider_id && (
                                                <button onClick={() => setFlagTarget({ type: "rider", name: ride.rider_name || "Rider" })}
                                                    className="flex items-center gap-1.5 text-xs font-semibold text-destructive hover:bg-destructive/10 px-3 py-2 rounded-lg border border-destructive/30 transition-colors">
                                                    <Flag className="h-3.5 w-3.5" /> Flag Rider
                                                </button>
                                            )}
                                            {ride.driver_id && (
                                                <button onClick={() => setFlagTarget({ type: "driver", name: ride.driver_name || "Driver" })}
                                                    className="flex items-center gap-1.5 text-xs font-semibold text-destructive hover:bg-destructive/10 px-3 py-2 rounded-lg border border-destructive/30 transition-colors">
                                                    <Flag className="h-3.5 w-3.5" /> Flag Driver
                                                </button>
                                            )}
                                            <button onClick={() => setShowComplaint(true)}
                                                className="flex items-center gap-1.5 text-xs font-semibold text-warning hover:bg-warning/10 px-3 py-2 rounded-lg border border-warning/30 transition-colors">
                                                <FileWarning className="h-3.5 w-3.5" /> Raise Complaint
                                            </button>
                                            {ride.status && !["completed", "cancelled"].includes(ride.status) && (
                                                <button onClick={() => { setCancelReason("Cancelled by admin"); setShowCancelDialog(true); }}
                                                    className="flex items-center gap-1.5 text-xs font-semibold text-destructive hover:bg-destructive/15 px-3 py-2 rounded-lg border border-destructive/40 transition-colors ml-auto">
                                                    <Ban className="h-3.5 w-3.5" /> Force Cancel
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                </Card>

                                {/* Metadata footer */}
                                <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-muted-foreground px-1">
                                    <p>Vehicle type: <span className="font-medium text-foreground/70">{ride.vehicle_type_id?.slice(0, 8) || "—"}</span></p>
                                    <p>OTP: <span className="font-mono font-medium text-foreground/70">{ride.pickup_otp || "—"}</span></p>
                                    {ride.shared_trip_token && <p>Shared trip: <span className="font-mono font-medium text-foreground/70">{ride.shared_trip_token}</span></p>}
                                </div>

                            </div>
                        </div>
                    )}
                </DialogContent>
            </Dialog>

            {flagTarget && (
                <RideFlagForm
                    open={!!flagTarget}
                    onClose={() => setFlagTarget(null)}
                    rideId={ride?.id || ""}
                    targetType={flagTarget.type}
                    targetName={flagTarget.name}
                    onFlagged={loadRide}
                />
            )}

            <RideComplaintForm
                open={showComplaint}
                onClose={() => setShowComplaint(false)}
                rideId={ride?.id || ""}
                onCreated={loadRide}
            />

            <Dialog open={showCancelDialog} onOpenChange={v => !v && setShowCancelDialog(false)}>
                <DialogContent className="max-w-md">
                    <DialogTitle>Force Cancel Ride</DialogTitle>
                    <p className="text-sm text-muted-foreground">
                        This will immediately cancel the ride and notify the rider and driver. This action cannot be undone.
                    </p>
                    <div className="space-y-2 py-2">
                        <label htmlFor="cancel-reason" className="text-sm font-medium">Reason (shown to rider &amp; driver)</label>
                        <input
                            id="cancel-reason"
                            className="w-full border rounded-lg px-3 py-2 text-sm"
                            value={cancelReason}
                            onChange={e => setCancelReason(e.target.value)}
                            placeholder="Cancelled by admin"
                        />
                    </div>
                    <div className="flex justify-end gap-2 pt-2">
                        <button
                            onClick={() => setShowCancelDialog(false)}
                            className="px-4 py-2 text-sm rounded-lg border hover:bg-muted transition-colors"
                        >
                            Back
                        </button>
                        <button
                            disabled={cancelling}
                            onClick={async () => {
                                if (!ride?.id) return;
                                setCancelling(true);
                                try {
                                    await adminCancelRide(ride.id, cancelReason.trim() || "Cancelled by admin");
                                    setShowCancelDialog(false);
                                    await loadRide();
                                } catch (err: any) {
                                    alert(err?.message || "Failed to cancel ride");
                                } finally {
                                    setCancelling(false);
                                }
                            }}
                            className="px-4 py-2 text-sm font-semibold rounded-lg bg-red-600 text-white hover:bg-red-700 disabled:opacity-50 transition-colors"
                        >
                            {cancelling ? "Cancelling..." : "Confirm Cancel"}
                        </button>
                    </div>
                </DialogContent>
            </Dialog>
        </>
    );
}
