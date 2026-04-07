"use client";

import { useEffect, useState, useRef, useCallback } from "react";
import { getLiveRideData } from "@/lib/api";
import { useParams, useRouter } from "next/navigation";
import { ArrowLeft, Phone, Car, User, MapPin, Radio, Navigation } from "lucide-react";
import dynamic from "next/dynamic";

const LiveRideMap = dynamic(() => import("./live-map"), { ssr: false });

export default function LiveRideTrackingPage() {
    const params = useParams();
    const router = useRouter();
    const rideId = params.id as string;
    const [ride, setRide] = useState<any>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");
    const intervalRef = useRef<NodeJS.Timeout | null>(null);
    const [trail, setTrail] = useState<{ lat: number; lng: number }[]>([]);

    const fetchData = useCallback(async () => {
        try {
            const data = await getLiveRideData(rideId);
            setRide(data);
            setError("");
            // Append driver location to trail
            if (data.driver_current_lat && data.driver_current_lng) {
                setTrail(prev => {
                    const last = prev[prev.length - 1];
                    if (!last || last.lat !== data.driver_current_lat || last.lng !== data.driver_current_lng) {
                        return [...prev, { lat: data.driver_current_lat, lng: data.driver_current_lng }];
                    }
                    return prev;
                });
            }
        } catch (e: any) {
            setError(e.message || "Failed to fetch ride data");
        } finally {
            setLoading(false);
        }
    }, [rideId]);

    useEffect(() => {
        fetchData();
        intervalRef.current = setInterval(fetchData, 5000);
        return () => {
            if (intervalRef.current) clearInterval(intervalRef.current);
        };
    }, [fetchData]);

    const isActive = ride && ["searching", "driver_assigned", "driver_arrived", "in_progress"].includes(ride.status);

    return (
        <div className="h-[calc(100vh-80px)] flex flex-col">
            {/* Header */}
            <div className="flex items-center gap-3 p-4 border-b bg-card">
                <button onClick={() => router.push("/dashboard/rides")} className="p-1.5 hover:bg-muted rounded-lg">
                    <ArrowLeft className="h-5 w-5" />
                </button>
                <div className="flex-1">
                    <div className="flex items-center gap-2">
                        <h1 className="text-lg font-bold">Live Tracking</h1>
                        {isActive && (
                            <span className="flex items-center gap-1 text-xs font-semibold text-blue-600 bg-blue-100 dark:bg-blue-900/30 px-2 py-0.5 rounded-md">
                                <Radio className="h-3 w-3 animate-pulse" /> LIVE
                            </span>
                        )}
                        {ride && !isActive && (
                            <span className="text-xs font-semibold text-muted-foreground bg-muted px-2 py-0.5 rounded-md">
                                {ride.status?.replace(/_/g, " ").toUpperCase()}
                            </span>
                        )}
                    </div>
                    <p className="text-[10px] text-muted-foreground font-mono">{rideId}</p>
                </div>
            </div>

            {loading ? (
                <div className="flex-1 flex items-center justify-center">
                    <div className="h-8 w-8 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                </div>
            ) : error ? (
                <div className="flex-1 flex items-center justify-center">
                    <p className="text-muted-foreground">{error}</p>
                </div>
            ) : ride ? (
                <div className="flex-1 flex flex-col lg:flex-row">
                    {/* Map */}
                    <div className="flex-1 relative">
                        <LiveRideMap
                            pickupLat={ride.pickup_lat}
                            pickupLng={ride.pickup_lng}
                            dropoffLat={ride.dropoff_lat}
                            dropoffLng={ride.dropoff_lng}
                            driverLat={ride.driver_current_lat}
                            driverLng={ride.driver_current_lng}
                            trail={trail}
                        />
                    </div>

                    {/* Info Panel */}
                    <div className="w-full lg:w-80 border-t lg:border-t-0 lg:border-l bg-card overflow-y-auto p-4 space-y-4">
                        {/* Status */}
                        <div className="bg-muted/30 rounded-xl p-3">
                            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-1">Status</p>
                            <p className="text-sm font-semibold">{ride.status?.replace(/_/g, " ").toUpperCase()}</p>
                        </div>

                        {/* Route */}
                        <div className="bg-muted/30 rounded-xl p-3 space-y-2">
                            <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Route</p>
                            <div className="flex gap-2 items-start">
                                <div className="w-2 h-2 rounded-full bg-emerald-500 mt-1.5 shrink-0" />
                                <p className="text-sm">{ride.pickup_address}</p>
                            </div>
                            <div className="flex gap-2 items-start">
                                <div className="w-2 h-2 rounded-full bg-primary mt-1.5 shrink-0" />
                                <p className="text-sm">{ride.dropoff_address}</p>
                            </div>
                        </div>

                        {/* Driver */}
                        {ride.driver_name && (
                            <div className="bg-muted/30 rounded-xl p-3 space-y-2">
                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Driver</p>
                                <div className="flex items-center gap-3">
                                    <div className="w-9 h-9 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center shrink-0">
                                        <Car className="h-4 w-4 text-emerald-600" />
                                    </div>
                                    <div>
                                        <p className="text-sm font-semibold">{ride.driver_name}</p>
                                        <p className="text-xs text-muted-foreground">{ride.driver_vehicle} {ride.driver_license_plate}</p>
                                        {ride.driver_phone && (
                                            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                                                <Phone className="h-3 w-3" />{ride.driver_phone}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Rider */}
                        {ride.rider_name && (
                            <div className="bg-muted/30 rounded-xl p-3 space-y-2">
                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider">Rider</p>
                                <div className="flex items-center gap-3">
                                    <div className="w-9 h-9 rounded-full bg-blue-100 dark:bg-blue-900/30 flex items-center justify-center shrink-0">
                                        <User className="h-4 w-4 text-blue-600" />
                                    </div>
                                    <div>
                                        <p className="text-sm font-semibold">{ride.rider_name}</p>
                                        {ride.rider_phone && (
                                            <p className="text-xs text-muted-foreground flex items-center gap-1 mt-0.5">
                                                <Phone className="h-3 w-3" />{ride.rider_phone}
                                            </p>
                                        )}
                                    </div>
                                </div>
                            </div>
                        )}

                        {/* Trail info */}
                        <div className="text-[10px] text-muted-foreground">
                            <p>GPS points tracked: {trail.length}</p>
                            <p>Auto-refreshing every 5s</p>
                        </div>
                    </div>
                </div>
            ) : null}
        </div>
    );
}
