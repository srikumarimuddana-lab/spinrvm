// src/app/dashboard/monitoring/ride-panel.tsx
"use client";

import { MonitoringRide } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Copy, MapPin, Phone, XCircle } from "lucide-react";

interface RidePanelProps {
    ride: MonitoringRide;
    onDriverClick: (driverId: string) => void;
    onCancelRide: (rideId: string) => void;
}

const STATUS_LABELS: Record<string, string> = {
    searching: "Searching",
    driver_assigned: "Driver Assigned",
    driver_arrived: "Driver Arrived",
    in_progress: "In Progress",
};

const STATUS_STEPS = ["searching", "driver_assigned", "driver_arrived", "in_progress"];

const STATUS_COLORS: Record<string, string> = {
    searching: "bg-yellow-500",
    driver_assigned: "bg-blue-500",
    driver_arrived: "bg-purple-500",
    in_progress: "bg-green-500",
};

export function RidePanel({ ride, onDriverClick, onCancelRide }: RidePanelProps) {
    const currentStepIdx = STATUS_STEPS.indexOf(ride.status);
    const elapsed = Math.floor(
        (Date.now() - new Date(ride.created_at).getTime()) / 60_000
    );

    function copyId() {
        navigator.clipboard.writeText(ride.id);
    }

    return (
        <div className="flex h-full flex-col overflow-y-auto">
            {/* Header */}
            <div className="flex items-start justify-between border-b border-border p-4">
                <div>
                    <div className="flex items-center gap-2">
                        <p className="font-mono text-xs text-muted-foreground">
                            #{ride.id.slice(-8)}
                        </p>
                        <button onClick={copyId}>
                            <Copy className="h-3 w-3 text-muted-foreground hover:text-foreground" />
                        </button>
                    </div>
                    <Badge
                        className={`mt-1 text-white ${STATUS_COLORS[ride.status] ?? "bg-gray-500"}`}
                    >
                        {STATUS_LABELS[ride.status] ?? ride.status}
                    </Badge>
                </div>
                {ride.is_corporate && (
                    <Badge variant="outline" className="text-xs">Corporate</Badge>
                )}
            </div>

            <div className="space-y-4 p-4">
                {/* Status timeline */}
                <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Progress
                    </p>
                    <div className="flex items-center gap-1">
                        {STATUS_STEPS.map((step, i) => (
                            <div key={step} className="flex flex-1 items-center">
                                <div
                                    className={`h-2 w-2 rounded-full ${
                                        i <= currentStepIdx ? "bg-primary" : "bg-muted"
                                    }`}
                                />
                                {i < STATUS_STEPS.length - 1 && (
                                    <div
                                        className={`h-0.5 flex-1 ${
                                            i < currentStepIdx ? "bg-primary" : "bg-muted"
                                        }`}
                                    />
                                )}
                            </div>
                        ))}
                    </div>
                    <div className="mt-1 flex justify-between">
                        {STATUS_STEPS.map((step) => (
                            <p key={step} className="w-16 text-center text-[10px] text-muted-foreground">
                                {STATUS_LABELS[step].split(" ")[0]}
                            </p>
                        ))}
                    </div>
                </div>

                {/* Route */}
                <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Route
                    </p>
                    <div className="rounded-lg border border-border p-3 text-xs">
                        <div className="flex gap-2">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-500" />
                            <p className="text-foreground">
                                {ride.pickup_address ?? `${ride.pickup_lat?.toFixed(4)}, ${ride.pickup_lng?.toFixed(4)}`}
                            </p>
                        </div>
                        <div className="my-1 ml-1.5 h-3 border-l border-dashed border-muted-foreground" />
                        <div className="flex gap-2">
                            <MapPin className="mt-0.5 h-3.5 w-3.5 shrink-0 text-red-500" />
                            <p className="text-foreground">
                                {ride.dropoff_address ?? `${ride.dropoff_lat?.toFixed(4)}, ${ride.dropoff_lng?.toFixed(4)}`}
                            </p>
                        </div>
                    </div>
                </div>

                {/* Stats */}
                <div className="grid grid-cols-3 gap-2 text-center text-xs">
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold">{elapsed}m</p>
                        <p className="text-muted-foreground">Elapsed</p>
                    </div>
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold">
                            {ride.distance_km ? `${ride.distance_km.toFixed(1)} km` : "—"}
                        </p>
                        <p className="text-muted-foreground">Distance</p>
                    </div>
                    <div className="rounded-lg bg-muted p-2">
                        <p className="font-bold text-green-600">
                            {ride.total_fare ? `$${ride.total_fare.toFixed(2)}` : "—"}
                        </p>
                        <p className="text-muted-foreground">Fare</p>
                    </div>
                </div>

                {/* People */}
                <div className="space-y-2">
                    <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                        Rider
                    </p>
                    <div className="flex items-center gap-2 rounded-lg border border-border p-2">
                        <Avatar className="h-8 w-8">
                            <AvatarFallback className="text-xs">
                                {ride.rider_name.slice(0, 2).toUpperCase()}
                            </AvatarFallback>
                        </Avatar>
                        <div className="flex-1 min-w-0">
                            <p className="truncate text-xs font-medium">{ride.rider_name}</p>
                            {ride.rider_phone && (
                                <p className="text-xs text-muted-foreground">{ride.rider_phone}</p>
                            )}
                        </div>
                        {ride.rider_phone && (
                            <Button
                                size="icon"
                                variant="ghost"
                                className="h-6 w-6"
                                onClick={() => window.open(`tel:${ride.rider_phone}`)}
                            >
                                <Phone className="h-3 w-3" />
                            </Button>
                        )}
                    </div>
                </div>

                {ride.driver_id && (
                    <div className="space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Driver
                        </p>
                        <div
                            className="flex cursor-pointer items-center gap-2 rounded-lg border border-border p-2 hover:bg-muted"
                            onClick={() => ride.driver_id && onDriverClick(ride.driver_id)}
                        >
                            <Avatar className="h-8 w-8">
                                <AvatarFallback className="text-xs">
                                    {(ride.driver_name ?? "DR").slice(0, 2).toUpperCase()}
                                </AvatarFallback>
                            </Avatar>
                            <div className="flex-1 min-w-0">
                                <p className="truncate text-xs font-medium">{ride.driver_name ?? "—"}</p>
                                {ride.driver_phone && (
                                    <p className="text-xs text-muted-foreground">{ride.driver_phone}</p>
                                )}
                            </div>
                            {ride.driver_phone && (
                                <Button
                                    size="icon"
                                    variant="ghost"
                                    className="h-6 w-6"
                                    onClick={(e) => {
                                        e.stopPropagation();
                                        window.open(`tel:${ride.driver_phone}`);
                                    }}
                                >
                                    <Phone className="h-3 w-3" />
                                </Button>
                            )}
                        </div>
                    </div>
                )}

                {/* Actions */}
                <Button
                    variant="destructive"
                    size="sm"
                    className="w-full gap-1.5 text-xs"
                    onClick={() => onCancelRide(ride.id)}
                >
                    <XCircle className="h-3.5 w-3.5" /> Cancel Ride
                </Button>
            </div>
        </div>
    );
}
