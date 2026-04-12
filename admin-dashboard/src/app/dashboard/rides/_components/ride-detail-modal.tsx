"use client";

import { useEffect, useState, useCallback } from "react";
import { getRideDetails } from "@/lib/api";
import { formatCurrency } from "@/lib/utils";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import {
    Car, User, Phone, Mail, Star, Route, Clock, Percent,
    DollarSign, Receipt, Ticket, AlertTriangle, Flag, Radio,
    MapPin, FileWarning, MapPinned, CalendarDays, Hash,
    Gauge, Shield, Users, Calendar,
} from "lucide-react";
import { Sec, FR, MStat, TL, getStatusBadge, fmtTime, isRideLive, computePhaseDistances } from "./ride-ui-helpers";
import RideInvoice from "./ride-invoice";
import RideLostFound from "./ride-lost-found";
import RideFlagForm from "./ride-flag-form";
import RideComplaintForm from "./ride-complaint-form";
import dynamic from "next/dynamic";

const RideRouteMap = dynamic(() => import("./ride-route-map"), { ssr: false });

const PHASE_LABELS: Record<string, string> = {
    navigating_to_pickup: "To Pickup",
    arrived_at_pickup: "At Pickup",
    trip_in_progress: "Trip",
    online_idle: "Idle",
    unknown: "Unknown",
};

const PHASE_COLORS: Record<string, string> = {
    navigating_to_pickup: "text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400",
    arrived_at_pickup: "text-violet-600 bg-violet-100 dark:bg-violet-900/30 dark:text-violet-400",
    trip_in_progress: "text-emerald-600 bg-emerald-100 dark:bg-emerald-900/30 dark:text-emerald-400",
    online_idle: "text-gray-600 bg-gray-100 dark:bg-gray-800/50 dark:text-gray-400",
    unknown: "text-gray-500 bg-gray-50 dark:bg-gray-800/30 dark:text-gray-500",
};

interface Props {
    rideId: string | null;
    open: boolean;
    onClose: () => void;
}

export default function RideDetailModal({ rideId, open, onClose }: Props) {
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(false);
    const [flagTarget, setFlagTarget] = useState<{ type: "rider" | "driver"; name: string } | null>(null);
    const [showComplaint, setShowComplaint] = useState(false);

    const loadRide = useCallback(async () => {
        if (!rideId) return;
        setLoading(true);
        try {
            const data = await getRideDetails(rideId);
            setRide(data);
        } catch { }
        finally { setLoading(false); }
    }, [rideId]);

    useEffect(() => {
        if (open && rideId) loadRide();
        if (!open) setRide(null);
    }, [open, rideId, loadRide]);

    if (!open) return null;

    // Prefer stored aggregates (set once at ride completion) over on-the-fly
    // computation from raw GPS points. Historical rides read directly from
    // the ride row — no join against driver_location_history needed.
    const storedPhases: Record<string, number> | null = ride?.phase_distances && Object.keys(ride.phase_distances).length > 0
        ? ride.phase_distances : null;

    const phaseDistances = storedPhases
        ? Object.entries(storedPhases).map(([phase, km]) => ({ phase, distance_km: Number(km) || 0, points: 0 }))
        : (ride?.location_trail ? computePhaseDistances(ride.location_trail) : []);

    const phaseMap: Record<string, number> = {};
    for (const p of phaseDistances) phaseMap[p.phase] = p.distance_km;

    // Fallback: if we still have nothing, derive from the ride record fields.
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

    return (
        <>
            <Dialog open={open} onOpenChange={v => !v && onClose()}>
                <DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-y-auto p-0" showCloseButton={true}>
                    <DialogTitle className="sr-only">
                        {ride ? `Ride ${ride.id}` : "Ride Details"}
                    </DialogTitle>
                    {loading || !ride ? (
                        <div className="flex flex-col items-center justify-center py-24">
                            <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            <p className="text-sm text-muted-foreground mt-3">Loading ride details...</p>
                        </div>
                    ) : (
                        <div className="divide-y divide-border">
                            {/* Header */}
                            <div className="px-6 py-5 bg-muted/20">
                                <div className="flex items-start justify-between gap-4">
                                    <div className="flex-1 min-w-0">
                                        <p className="text-[11px] text-muted-foreground font-mono mb-1.5">Ride ID: {ride.id}</p>
                                        <div className="flex items-center gap-2.5 flex-wrap">
                                            {getStatusBadge(ride.status)}
                                            {isRideLive(ride.status) && (
                                                <a href={`/dashboard/rides/live/${ride.id}`}
                                                    className="flex items-center gap-1.5 text-xs font-semibold text-blue-600 bg-blue-100 dark:bg-blue-900/30 dark:text-blue-400 px-2.5 py-1 rounded-lg hover:bg-blue-200 dark:hover:bg-blue-900/50 transition-colors">
                                                    <Radio className="h-3 w-3 animate-pulse" /> Live Track
                                                </a>
                                            )}
                                            <RideInvoice rideId={ride.id} status={ride.status} />
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Route + Map */}
                            <div className="px-6 py-5">
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                                    <Sec title="Route">
                                        <div className="flex gap-3">
                                            <div className="flex flex-col items-center pt-1">
                                                <div className="w-2.5 h-2.5 rounded-full bg-emerald-500 ring-2 ring-emerald-200 dark:ring-emerald-900/50" />
                                                <div className="w-0.5 flex-1 bg-border my-1.5" />
                                                <div className="w-2.5 h-2.5 rounded-full bg-primary ring-2 ring-primary/20" />
                                            </div>
                                            <div className="flex-1">
                                                <p className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Pickup</p>
                                                <p className="text-sm font-medium mt-0.5">{ride.pickup_address || "—"}</p>
                                                <div className="h-4" />
                                                <p className="text-[10px] text-muted-foreground uppercase font-bold tracking-wider">Dropoff</p>
                                                <p className="text-sm font-medium mt-0.5">{ride.dropoff_address || "—"}</p>
                                            </div>
                                        </div>
                                        <div className="flex gap-2 mt-3 pt-3 border-t">
                                            <MStat label="Distance" value={`${(ride.distance_km || 0).toFixed(1)} km`} icon={Route} />
                                            <MStat label="Duration" value={`${ride.duration_minutes || 0} min`} icon={Clock} />
                                            <MStat label="Surge" value={`${ride.surge_multiplier || 1.0}x`} icon={Percent} />
                                        </div>
                                    </Sec>
                                    <div>
                                        <h4 className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider mb-2.5">Map</h4>
                                        {ride.pickup_lat && ride.dropoff_lat ? (
                                            <RideRouteMap
                                                pickupLat={ride.pickup_lat} pickupLng={ride.pickup_lng}
                                                dropoffLat={ride.dropoff_lat} dropoffLng={ride.dropoff_lng}
                                                locationTrail={
                                                    // Prefer stored downsampled polyline (set on completion)
                                                    // over raw trail so we don't hit driver_location_history
                                                    Array.isArray(ride.route_polyline) && ride.route_polyline.length > 0
                                                        ? ride.route_polyline.map((p: any) => ({ lat: p[0], lng: p[1] }))
                                                        : ride.location_trail
                                                }
                                            />
                                        ) : (
                                            <div className="bg-muted/30 rounded-xl h-[280px] flex flex-col items-center justify-center gap-2">
                                                <MapPin className="h-8 w-8 text-muted-foreground/20" />
                                                <p className="text-xs text-muted-foreground">No map data available</p>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>

                            {/* Customer & Driver */}
                            <div className="px-6 py-5">
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                                    {/* Customer Section */}
                                    <Sec title="Customer">
                                        <div className="flex items-center gap-3">
                                            <div className="w-11 h-11 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0 ring-2 ring-blue-200/50 dark:ring-blue-800/30">
                                                {ride.rider_profile_image ? (
                                                    <img src={ride.rider_profile_image} alt="" className="w-11 h-11 rounded-full object-cover" />
                                                ) : (
                                                    <User className="h-5 w-5 text-blue-600 dark:text-blue-400" />
                                                )}
                                            </div>
                                            <div className="flex-1 min-w-0">
                                                <p className="text-sm font-semibold">{ride.rider_name || ride.rider_id?.slice(0, 12) || "—"}</p>
                                                <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                                    {ride.rider_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{ride.rider_phone}</span>}
                                                    {ride.rider_email && <span className="flex items-center gap-1"><Mail className="h-3 w-3" />{ride.rider_email}</span>}
                                                </div>
                                            </div>
                                            <div className="flex items-center gap-2 shrink-0">
                                                {ride.rider_flag_count > 0 && (
                                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${ride.rider_flag_count >= 2 ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"}`}>
                                                        {ride.rider_flag_count} flag{ride.rider_flag_count > 1 ? "s" : ""}
                                                    </span>
                                                )}
                                            </div>
                                        </div>
                                        <div className="grid grid-cols-3 gap-2 mt-3 pt-3 border-t">
                                            <div className="bg-background rounded-lg p-2.5 text-center">
                                                <Hash className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                <p className="text-xs font-bold">{ride.rider_total_rides ?? "—"}</p>
                                                <p className="text-[9px] text-muted-foreground">Total Rides</p>
                                            </div>
                                            <div className="bg-background rounded-lg p-2.5 text-center">
                                                <MapPinned className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                <p className="text-xs font-bold truncate">{ride.rider_region || ride.rider_city || "—"}</p>
                                                <p className="text-[9px] text-muted-foreground">Region</p>
                                            </div>
                                            <div className="bg-background rounded-lg p-2.5 text-center">
                                                <CalendarDays className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                <p className="text-xs font-bold">{ride.rider_joined ? new Date(ride.rider_joined).toLocaleDateString("en-CA", { month: "short", year: "numeric" }) : "—"}</p>
                                                <p className="text-[9px] text-muted-foreground">Member Since</p>
                                            </div>
                                        </div>
                                    </Sec>

                                    {/* Driver Section */}
                                    <Sec title="Driver">
                                        {ride.driver_id ? (
                                            <>
                                                <div className="flex items-center gap-3">
                                                    <div className="w-11 h-11 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center shrink-0 ring-2 ring-emerald-200/50 dark:ring-emerald-800/30">
                                                        {ride.driver_photo_url ? (
                                                            <img src={ride.driver_photo_url} alt="" className="w-11 h-11 rounded-full object-cover" />
                                                        ) : (
                                                            <Car className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
                                                        )}
                                                    </div>
                                                    <div className="flex-1 min-w-0">
                                                        <p className="text-sm font-semibold">{ride.driver_name || ride.driver_id?.slice(0, 12)}</p>
                                                        <div className="flex items-center gap-3 text-xs text-muted-foreground mt-0.5 flex-wrap">
                                                            {ride.driver_phone && <span className="flex items-center gap-1"><Phone className="h-3 w-3" />{ride.driver_phone}</span>}
                                                            {ride.driver_license_plate && <span className="font-mono font-bold text-foreground bg-muted px-1.5 py-0.5 rounded text-[11px]">{ride.driver_license_plate}</span>}
                                                        </div>
                                                    </div>
                                                    <div className="flex items-center gap-2 shrink-0">
                                                        {ride.driver_flag_count > 0 && (
                                                            <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${ride.driver_flag_count >= 2 ? "bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"}`}>
                                                                {ride.driver_flag_count} flag{ride.driver_flag_count > 1 ? "s" : ""}
                                                            </span>
                                                        )}
                                                        {ride.driver_rating != null && (
                                                            <span className="flex items-center gap-0.5 bg-amber-50 dark:bg-amber-900/20 px-2 py-0.5 rounded-md">
                                                                <Star className="h-3.5 w-3.5 text-amber-400 fill-amber-400" />
                                                                <span className="text-xs font-bold">{Number(ride.driver_rating).toFixed(1)}</span>
                                                            </span>
                                                        )}
                                                    </div>
                                                </div>
                                                {/* Vehicle details */}
                                                <div className="mt-3 pt-3 border-t space-y-2">
                                                    <div className="flex items-center gap-2 text-xs">
                                                        <Car className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                                        <span className="font-medium">
                                                            {[ride.driver_vehicle_color, ride.driver_vehicle_year, ride.driver_vehicle_make, ride.driver_vehicle_model].filter(Boolean).join(" ") || "—"}
                                                        </span>
                                                    </div>
                                                    {ride.driver_vehicle_type_name && (
                                                        <div className="flex items-center gap-2 text-xs">
                                                            <Users className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                                            <span>{ride.driver_vehicle_type_name}</span>
                                                            {ride.driver_vehicle_capacity > 0 && (
                                                                <span className="text-muted-foreground">({ride.driver_vehicle_capacity} seats)</span>
                                                            )}
                                                        </div>
                                                    )}
                                                    {ride.driver_vehicle_vin && (
                                                        <div className="flex items-center gap-2 text-xs">
                                                            <Shield className="h-3.5 w-3.5 text-muted-foreground shrink-0" />
                                                            <span className="font-mono text-muted-foreground">VIN: {ride.driver_vehicle_vin}</span>
                                                        </div>
                                                    )}
                                                </div>
                                                {/* Driver stats */}
                                                <div className="grid grid-cols-4 gap-2 mt-3 pt-3 border-t">
                                                    <div className="bg-background rounded-lg p-2.5 text-center">
                                                        <Hash className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                        <p className="text-xs font-bold">{ride.driver_total_rides ?? ride.driver_completed_rides ?? "—"}</p>
                                                        <p className="text-[9px] text-muted-foreground">Total Rides</p>
                                                    </div>
                                                    <div className="bg-background rounded-lg p-2.5 text-center">
                                                        <Gauge className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                        <p className="text-xs font-bold">{ride.driver_acceptance_rate != null ? `${ride.driver_acceptance_rate}%` : "—"}</p>
                                                        <p className="text-[9px] text-muted-foreground">Completion</p>
                                                    </div>
                                                    <div className="bg-background rounded-lg p-2.5 text-center">
                                                        <Star className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                        <p className="text-xs font-bold">{ride.driver_rating ? Number(ride.driver_rating).toFixed(1) : "—"}</p>
                                                        <p className="text-[9px] text-muted-foreground">Rating</p>
                                                    </div>
                                                    <div className="bg-background rounded-lg p-2.5 text-center">
                                                        <MapPinned className="h-3.5 w-3.5 text-muted-foreground mx-auto mb-0.5" />
                                                        <p className="text-xs font-bold truncate">{ride.driver_region || ride.driver_city || "—"}</p>
                                                        <p className="text-[9px] text-muted-foreground">Region</p>
                                                    </div>
                                                </div>
                                            </>
                                        ) : (
                                            <div className="flex flex-col items-center py-4 text-muted-foreground">
                                                <Car className="h-6 w-6 opacity-30 mb-2" />
                                                <p className="text-sm">No driver assigned</p>
                                            </div>
                                        )}
                                    </Sec>
                                </div>
                            </div>

                            {/* Fare & Revenue */}
                            <div className="px-6 py-5">
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                                    <Sec title="Fare Breakdown">
                                        <FR l="Base fare" v={ride.base_fare} />
                                        <FR l={`Distance (${(ride.distance_km || 0).toFixed(1)} km)`} v={ride.distance_fare} />
                                        <FR l={`Time (${ride.duration_minutes || 0} min)`} v={ride.time_fare} />
                                        <FR l="Booking fee" v={ride.booking_fee} />
                                        {(ride.airport_fee || 0) > 0 && <FR l="Airport fee" v={ride.airport_fee} />}
                                        <div className="border-t my-2" />
                                        <FR l="Subtotal" v={ride.total_fare} b />
                                        {((ride.tip_amount || 0) > 0 || ride.promo_code) && (
                                            <>
                                                <div className="border-t my-2" />
                                                {ride.promo_code && (
                                                    <div className="flex justify-between items-center">
                                                        <span className="flex items-center gap-2 text-sm"><Ticket className="h-4 w-4 text-violet-500" />Promo: <b className="font-mono text-xs bg-violet-50 dark:bg-violet-900/20 px-1.5 py-0.5 rounded">{ride.promo_code}</b></span>
                                                        <span className="text-sm font-semibold text-emerald-600">-{formatCurrency(ride.promo_discount || 0)}</span>
                                                    </div>
                                                )}
                                                {(ride.tip_amount || 0) > 0 && (
                                                    <div className="flex justify-between items-center">
                                                        <span className="flex items-center gap-2 text-sm"><DollarSign className="h-4 w-4 text-amber-500" />Tip</span>
                                                        <span className="text-sm font-semibold text-amber-600">{formatCurrency(ride.tip_amount)}</span>
                                                    </div>
                                                )}
                                            </>
                                        )}
                                    </Sec>
                                    <Sec title="Revenue Split">
                                        <div className="grid grid-cols-3 gap-2.5">
                                            <div className="bg-emerald-50 dark:bg-emerald-900/20 rounded-xl p-3.5 text-center">
                                                <p className="text-lg font-extrabold text-emerald-600 dark:text-emerald-400">{formatCurrency(ride.driver_earnings || 0)}</p>
                                                <p className="text-[11px] text-muted-foreground mt-1 font-medium">Driver</p>
                                            </div>
                                            <div className="bg-violet-50 dark:bg-violet-900/20 rounded-xl p-3.5 text-center">
                                                <p className="text-lg font-extrabold text-violet-600 dark:text-violet-400">{formatCurrency(ride.admin_earnings || 0)}</p>
                                                <p className="text-[11px] text-muted-foreground mt-1 font-medium">Platform</p>
                                            </div>
                                            <div className="bg-amber-50 dark:bg-amber-900/20 rounded-xl p-3.5 text-center">
                                                <p className="text-lg font-extrabold text-amber-600 dark:text-amber-400">{formatCurrency(ride.tip_amount || 0)}</p>
                                                <p className="text-[11px] text-muted-foreground mt-1 font-medium">Tip</p>
                                            </div>
                                        </div>
                                        <div className="flex justify-between items-center mt-3 pt-3 border-t">
                                            <span className="text-sm font-bold">Total Charged</span>
                                            <span className="text-lg font-extrabold text-primary">
                                                {formatCurrency((ride.total_fare || 0) + (ride.tip_amount || 0) - (ride.promo_discount || 0))}
                                            </span>
                                        </div>
                                        <div className="mt-2.5 pt-2.5 border-t space-y-1.5">
                                            <FR l="Rider paid" v={(ride.total_fare || 0) + (ride.tip_amount || 0) - (ride.promo_discount || 0)} />
                                            <FR l="Driver gets" v={(ride.driver_earnings || 0) + (ride.tip_amount || 0)} />
                                            <FR l="Admin gets" v={ride.admin_earnings || 0} />
                                        </div>
                                    </Sec>
                                </div>
                            </div>

                            {/* Payment & Rating */}
                            <div className="px-6 py-5">
                                <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                                    <Sec title="Payment">
                                        <div className="flex items-center justify-between">
                                            <span className="flex items-center gap-2 text-sm font-medium"><Receipt className="h-4 w-4 text-muted-foreground" />{ride.payment_method || "Card"}</span>
                                            <span className={`text-xs font-bold px-2.5 py-1 rounded-md ${ride.payment_status === "paid" ? "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/30 dark:text-emerald-400" : "bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400"}`}>
                                                {(ride.payment_status || "pending").toUpperCase()}
                                            </span>
                                        </div>
                                    </Sec>
                                    <Sec title="Rating">
                                        {ride.rider_rating ? (
                                            <div>
                                                <div className="flex items-center gap-1">
                                                    {[1, 2, 3, 4, 5].map(i => (
                                                        <Star key={i} className={`h-4.5 w-4.5 ${i <= ride.rider_rating ? "text-amber-400 fill-amber-400" : "text-gray-200 dark:text-gray-700"}`} />
                                                    ))}
                                                    <span className="text-sm font-bold ml-2">{ride.rider_rating}/5</span>
                                                </div>
                                                {ride.rider_comment && <p className="text-sm text-muted-foreground mt-2 italic">"{ride.rider_comment}"</p>}
                                            </div>
                                        ) : (
                                            <p className="text-sm text-muted-foreground py-1">No rating yet</p>
                                        )}
                                    </Sec>
                                </div>
                            </div>

                            {/* Cancellation */}
                            {ride.status === "cancelled" && (
                                <div className="px-6 py-5">
                                    <Sec title="Cancellation">
                                        <FR l="Reason" v={ride.cancellation_reason || "—"} t />
                                        <FR l="Driver fee" v={ride.cancellation_fee_driver} />
                                        <FR l="Admin fee" v={ride.cancellation_fee_admin} />
                                    </Sec>
                                </div>
                            )}

                            {/* Lost & Found */}
                            <div className="px-6 py-5">
                                <RideLostFound rideId={ride.id} items={ride.lost_and_found || []} onRefresh={loadRide} />
                            </div>

                            {/* Complaints & Flags */}
                            <div className="px-6 py-5">
                                <Sec title="Complaints & Flags">
                                    {ride.flags && ride.flags.length > 0 ? (
                                        <div className="space-y-2 mb-3">
                                            {ride.flags.map((f: any, i: number) => (
                                                <div key={f.id || i} className="flex items-center gap-2.5 bg-background rounded-lg p-2.5">
                                                    <Flag className={`h-3.5 w-3.5 shrink-0 ${f._party === "rider" ? "text-blue-500" : "text-emerald-500"}`} />
                                                    <span className="text-xs font-semibold">{f._party === "rider" ? "Rider" : "Driver"}</span>
                                                    <span className="text-xs bg-red-100 text-red-700 dark:bg-red-900/30 dark:text-red-400 px-1.5 py-0.5 rounded-md font-medium">{f.reason?.replace(/_/g, " ")}</span>
                                                    {f.description && <span className="text-xs text-muted-foreground truncate flex-1">{f.description}</span>}
                                                    <span className="text-[10px] text-muted-foreground tabular-nums">{fmtTime(f.created_at)}</span>
                                                </div>
                                            ))}
                                        </div>
                                    ) : (
                                        <p className="text-sm text-muted-foreground mb-3">No flags or complaints</p>
                                    )}

                                    {ride.complaints && ride.complaints.length > 0 && (
                                        <div className="space-y-2 mb-3">
                                            {ride.complaints.map((c: any) => (
                                                <div key={c.id} className="flex items-center gap-2.5 bg-background rounded-lg p-2.5">
                                                    <FileWarning className="h-3.5 w-3.5 shrink-0 text-amber-500" />
                                                    <span className="text-xs font-semibold">{c.against_type}</span>
                                                    <span className="text-xs bg-amber-100 text-amber-700 dark:bg-amber-900/30 dark:text-amber-400 px-1.5 py-0.5 rounded-md font-medium">{c.category}</span>
                                                    <span className="text-xs text-muted-foreground truncate flex-1">{c.description}</span>
                                                    <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded-md ${c.status === "open" ? "bg-amber-100 text-amber-700" : "bg-emerald-100 text-emerald-700"}`}>{c.status}</span>
                                                </div>
                                            ))}
                                        </div>
                                    )}

                                    {(ride.rider_flag_count >= 2 || ride.driver_flag_count >= 2) && (
                                        <div className="flex items-center gap-2.5 bg-red-50 dark:bg-red-900/20 rounded-lg p-3 mb-3">
                                            <AlertTriangle className="h-4 w-4 text-red-500 shrink-0" />
                                            <p className="text-xs font-semibold text-red-600 dark:text-red-400">
                                                {ride.rider_flag_count >= 2 && `Rider has ${ride.rider_flag_count} flags (1 more = ban). `}
                                                {ride.driver_flag_count >= 2 && `Driver has ${ride.driver_flag_count} flags (1 more = ban).`}
                                            </p>
                                        </div>
                                    )}

                                    <div className="flex gap-2 flex-wrap pt-1">
                                        {ride.rider_id && (
                                            <button onClick={() => setFlagTarget({ type: "rider", name: ride.rider_name || "Rider" })}
                                                className="flex items-center gap-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 px-3 py-2 rounded-lg border border-red-200 dark:border-red-900/30 transition-colors">
                                                <Flag className="h-3.5 w-3.5" /> Flag Rider
                                            </button>
                                        )}
                                        {ride.driver_id && (
                                            <button onClick={() => setFlagTarget({ type: "driver", name: ride.driver_name || "Driver" })}
                                                className="flex items-center gap-1.5 text-xs font-semibold text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20 px-3 py-2 rounded-lg border border-red-200 dark:border-red-900/30 transition-colors">
                                                <Flag className="h-3.5 w-3.5" /> Flag Driver
                                            </button>
                                        )}
                                        <button onClick={() => setShowComplaint(true)}
                                            className="flex items-center gap-1.5 text-xs font-semibold text-amber-600 hover:bg-amber-50 dark:hover:bg-amber-900/20 px-3 py-2 rounded-lg border border-amber-200 dark:border-amber-900/30 transition-colors">
                                            <FileWarning className="h-3.5 w-3.5" /> Raise Complaint
                                        </button>
                                    </div>
                                </Sec>
                            </div>

                            {/* Booking Logs */}
                            <div className="px-6 py-5">
                                <Sec title="Booking Logs">
                                    <div className="space-y-0.5">
                                        <TL l="Requested" t={ride.ride_requested_at || ride.created_at} />
                                        <TL l="Driver notified" t={ride.driver_notified_at} />
                                        <TL l="Driver accepted" t={ride.driver_accepted_at}
                                            km={phaseMap.navigating_to_pickup} />
                                        <TL l="Driver arrived" t={ride.driver_arrived_at}
                                            km={phaseMap.arrived_at_pickup} />
                                        <TL l="Ride started" t={ride.ride_started_at} />
                                        <TL l="Ride completed" t={ride.ride_completed_at}
                                            km={phaseMap.trip_in_progress} />
                                        {ride.cancelled_at && <TL l="Cancelled" t={ride.cancelled_at} d />}
                                    </div>

                                    {/* Phase distance summary */}
                                    {phaseDistances.length > 0 && (
                                        <div className="mt-3 pt-3 border-t">
                                            <p className="text-[11px] font-bold text-muted-foreground uppercase tracking-wider mb-2.5">Distance by Phase</p>
                                            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                                                {phaseDistances.map(p => (
                                                    <div key={p.phase} className={`rounded-lg px-3 py-2.5 ${PHASE_COLORS[p.phase] || "bg-gray-50 text-gray-600 dark:bg-gray-800/30 dark:text-gray-400"}`}>
                                                        <p className="text-xs font-bold">{p.distance_km} km</p>
                                                        <p className="text-[10px] opacity-80">{PHASE_LABELS[p.phase] || p.phase.replace(/_/g, " ")}</p>
                                                        <p className="text-[9px] opacity-60">{p.points} pts</p>
                                                    </div>
                                                ))}
                                            </div>
                                            <div className="flex justify-between mt-2.5 text-xs">
                                                <span className="text-muted-foreground">Total GPS distance</span>
                                                <span className="font-bold tabular-nums">{phaseDistances.reduce((s, p) => s + p.distance_km, 0).toFixed(2)} km</span>
                                            </div>
                                        </div>
                                    )}
                                </Sec>
                            </div>

                            {/* Driver Tracking */}
                            {ride.location_trail && ride.location_trail.length > 0 && (
                                <div className="px-6 py-5">
                                    <Sec title="Driver Tracking">
                                        <p className="text-xs text-muted-foreground mb-2.5">{ride.location_trail.length} GPS points recorded</p>
                                        <div className="max-h-[200px] overflow-y-auto rounded-lg border">
                                            <table className="w-full text-xs">
                                                <thead className="sticky top-0 bg-muted/80 backdrop-blur-sm">
                                                    <tr className="text-muted-foreground">
                                                        <th className="text-left py-2 px-3 font-semibold">Time</th>
                                                        <th className="text-left py-2 px-3 font-semibold">Lat</th>
                                                        <th className="text-left py-2 px-3 font-semibold">Lng</th>
                                                        <th className="text-left py-2 px-3 font-semibold">Speed</th>
                                                        <th className="text-left py-2 px-3 font-semibold">Phase</th>
                                                    </tr>
                                                </thead>
                                                <tbody className="divide-y divide-border/50">
                                                    {ride.location_trail.slice(0, 100).map((pt: any, i: number) => (
                                                        <tr key={i} className="hover:bg-muted/30 transition-colors">
                                                            <td className="py-1.5 px-3 tabular-nums">{fmtTime(pt.timestamp)}</td>
                                                            <td className="py-1.5 px-3 font-mono">{pt.lat?.toFixed(5)}</td>
                                                            <td className="py-1.5 px-3 font-mono">{pt.lng?.toFixed(5)}</td>
                                                            <td className="py-1.5 px-3 tabular-nums">{pt.speed != null ? `${pt.speed.toFixed(1)} km/h` : "—"}</td>
                                                            <td className="py-1.5 px-3">{pt.tracking_phase?.replace(/_/g, " ") || "—"}</td>
                                                        </tr>
                                                    ))}
                                                </tbody>
                                            </table>
                                            {ride.location_trail.length > 100 && (
                                                <p className="text-[10px] text-muted-foreground text-center py-2 bg-muted/30">Showing first 100 of {ride.location_trail.length} points</p>
                                            )}
                                        </div>
                                    </Sec>
                                </div>
                            )}

                            {/* Metadata */}
                            <div className="px-6 py-4 bg-muted/20">
                                <div className="flex flex-wrap gap-x-6 gap-y-1 text-[11px] text-muted-foreground">
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
        </>
    );
}
