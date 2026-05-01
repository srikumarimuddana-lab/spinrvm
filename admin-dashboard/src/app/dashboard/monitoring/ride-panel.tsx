// src/app/dashboard/monitoring/ride-panel.tsx
"use client";

import { MonitoringRide } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { 
    Copy, 
    Phone, 
    XCircle, 
    CheckCircle, 
    Clock, 
    Navigation, 
    Banknote, 
    User, 
    Car, 
    ChevronRight, 
    MapPinned 
} from "lucide-react";

interface RidePanelProps {
    ride: MonitoringRide;
    onDriverClick: (driverId: string) => void;
    onCancelRide: (rideId: string) => void;
    onCompleteRide?: (rideId: string) => void;
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

export function RidePanel({ ride, onDriverClick, onCancelRide, onCompleteRide }: RidePanelProps) {
    const currentStepIdx = STATUS_STEPS.indexOf(ride.status);
    const elapsed = Math.floor(
        (Date.now() - new Date(ride.created_at).getTime()) / 60_000
    );

    function copyId() {
        navigator.clipboard.writeText(ride.id);
    }

    return (
        <div className="flex h-full flex-col bg-muted/30">
            {/* Header */}
            <div className="flex flex-col gap-3 border-b bg-background p-5 shadow-sm">
                <div className="flex items-start justify-between">
                    <div className="space-y-1.5">
                        <div className="flex items-center gap-2 text-muted-foreground">
                            <span className="text-xs font-semibold uppercase tracking-wider">Ride ID</span>
                            <div className="flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs font-mono text-foreground">
                                {ride.id.slice(-8)}
                                <button onClick={copyId} className="hover:text-primary transition-colors">
                                    <Copy className="h-3 w-3" />
                                </button>
                            </div>
                        </div>
                        <h2 className="text-xl font-bold text-foreground">
                            {STATUS_LABELS[ride.status] ?? ride.status}
                        </h2>
                    </div>
                    <div className="flex flex-col items-end gap-2">
                        <Badge
                            className={`px-3 py-1.5 text-xs font-semibold text-white shadow-sm ${STATUS_COLORS[ride.status] ?? "bg-gray-500"}`}
                        >
                            {STATUS_LABELS[ride.status] ?? ride.status}
                        </Badge>
                        {ride.is_corporate && (
                            <Badge variant="outline" className="border-primary/40 text-primary bg-primary/5 text-[10px] font-bold uppercase tracking-wider">
                                Corporate
                            </Badge>
                        )}
                    </div>
                </div>
            </div>

            <div className="flex-1 space-y-5 overflow-y-auto p-5 pb-24">
                {/* Status timeline */}
                <div className="rounded-xl border bg-card p-5 shadow-sm">
                    <p className="mb-4 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                        <Clock className="h-3.5 w-3.5" /> Trip Progress
                    </p>
                    <div className="relative flex items-center justify-between px-2">
                        {STATUS_STEPS.map((step, i) => {
                            const isActive = i <= currentStepIdx;
                            const isCurrent = i === currentStepIdx;
                            return (
                                <div key={step} className="flex flex-col items-center gap-2.5 z-10">
                                    <div
                                        className={`flex h-7 w-7 items-center justify-center rounded-full ring-4 ring-card transition-all duration-300 ${
                                            isActive ? "bg-primary text-primary-foreground shadow-md" : "bg-muted text-muted-foreground"
                                        } ${isCurrent ? "ring-primary/20 scale-110" : ""}`}
                                    >
                                        {isActive ? <CheckCircle className="h-4 w-4" /> : <div className="h-2.5 w-2.5 rounded-full bg-current opacity-40" />}
                                    </div>
                                    <span className={`text-[10px] font-bold uppercase tracking-wider ${isActive ? "text-foreground" : "text-muted-foreground"}`}>
                                        {STATUS_LABELS[step].split(" ")[0]}
                                    </span>
                                </div>
                            );
                        })}
                        {/* Connecting Lines */}
                        <div className="absolute left-2 right-2 top-3.5 -z-0 h-1 bg-muted rounded-full overflow-hidden">
                            <div
                                className="h-full bg-primary transition-all duration-500 ease-in-out"
                                style={{ width: `${(currentStepIdx / (STATUS_STEPS.length - 1)) * 100}%` }}
                            />
                        </div>
                    </div>
                </div>

                {/* Route */}
                <div className="rounded-xl border bg-card p-5 shadow-sm">
                    <p className="mb-4 flex items-center gap-2 text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                        <MapPinned className="h-3.5 w-3.5" /> Route Details
                    </p>
                    <div className="relative ml-3 space-y-6 border-l-2 border-dashed border-muted pl-6 py-1">
                        <div className="relative">
                            <div className="absolute -left-[33px] top-1 flex h-4 w-4 items-center justify-center rounded-full bg-blue-100 ring-4 ring-card">
                                <div className="h-2 w-2 rounded-full bg-blue-600" />
                            </div>
                            <p className="text-[10px] font-bold uppercase tracking-wider text-blue-600">Pickup</p>
                            <p className="mt-1 text-sm font-medium text-foreground leading-snug">
                                {ride.pickup_address ?? `${ride.pickup_lat?.toFixed(4)}, ${ride.pickup_lng?.toFixed(4)}`}
                            </p>
                        </div>
                        <div className="relative">
                            <div className="absolute -left-[33px] top-1 flex h-4 w-4 items-center justify-center rounded-full bg-red-100 ring-4 ring-card">
                                <div className="h-2 w-2 rounded-full bg-red-600" />
                            </div>
                            <p className="text-[10px] font-bold uppercase tracking-wider text-red-600">Dropoff</p>
                            <p className="mt-1 text-sm font-medium text-foreground leading-snug">
                                {ride.dropoff_address ?? `${ride.dropoff_lat?.toFixed(4)}, ${ride.dropoff_lng?.toFixed(4)}`}
                            </p>
                        </div>
                    </div>
                </div>

                {/* Stats */}
                <div className="grid grid-cols-3 gap-3">
                    <div className="flex flex-col items-center justify-center rounded-xl border bg-card p-4 shadow-sm transition-colors hover:bg-muted/30">
                        <Clock className="mb-2 h-5 w-5 text-muted-foreground" />
                        <p className="text-xl font-black text-foreground tracking-tight">{elapsed}<span className="text-sm font-medium text-muted-foreground ml-0.5">m</span></p>
                        <p className="mt-0.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Elapsed</p>
                    </div>
                    <div className="flex flex-col items-center justify-center rounded-xl border bg-card p-4 shadow-sm transition-colors hover:bg-muted/30">
                        <Navigation className="mb-2 h-5 w-5 text-muted-foreground" />
                        <p className="text-xl font-black text-foreground tracking-tight">
                            {ride.distance_km ? `${ride.distance_km.toFixed(1)}` : "—"}<span className="text-sm font-medium text-muted-foreground ml-0.5">km</span>
                        </p>
                        <p className="mt-0.5 text-[10px] font-bold uppercase tracking-wider text-muted-foreground">Distance</p>
                    </div>
                    <div className="flex flex-col items-center justify-center rounded-xl border bg-green-50/50 p-4 shadow-sm ring-1 ring-inset ring-green-500/30">
                        <Banknote className="mb-2 h-5 w-5 text-green-600" />
                        <p className="text-xl font-black text-green-700 tracking-tight">
                            {ride.total_fare ? `$${ride.total_fare.toFixed(2)}` : "—"}
                        </p>
                        <p className="mt-0.5 text-[10px] font-bold uppercase tracking-wider text-green-700/80">Total Fare</p>
                    </div>
                </div>

                {/* People */}
                <div className="rounded-xl border bg-card shadow-sm overflow-hidden">
                    <div className="bg-muted/40 px-5 py-3 border-b">
                        <p className="text-[11px] font-bold uppercase tracking-wider text-muted-foreground">
                            Participants
                        </p>
                    </div>
                    <div className="p-3 space-y-1.5">
                        <div className="flex items-center gap-4 rounded-lg p-3 transition-colors hover:bg-muted/50">
                            <div className="relative">
                                <Avatar className="h-12 w-12 border-2 border-background shadow-sm">
                                    <AvatarFallback className="bg-blue-100 text-blue-700 font-bold text-sm">
                                        {ride.rider_name.slice(0, 2).toUpperCase()}
                                    </AvatarFallback>
                                </Avatar>
                                <div className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-blue-500 ring-2 ring-background">
                                    <User className="h-3 w-3 text-white" />
                                </div>
                            </div>
                            <div className="flex-1 min-w-0">
                                <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">Rider</p>
                                <p className="truncate text-base font-semibold text-foreground">{ride.rider_name}</p>
                            </div>
                            {ride.rider_phone && (
                                <Button
                                    size="icon"
                                    variant="outline"
                                    className="h-10 w-10 rounded-full shadow-sm hover:bg-blue-50 hover:text-blue-600 hover:border-blue-200 transition-colors"
                                    onClick={() => window.open(`tel:${ride.rider_phone}`)}
                                >
                                    <Phone className="h-4 w-4" />
                                </Button>
                            )}
                        </div>

                        {ride.driver_id && (
                            <>
                                <div className="mx-4 my-1.5 h-px bg-border" />
                                <div
                                    className="flex cursor-pointer items-center gap-4 rounded-lg p-3 transition-all hover:bg-muted/80 active:scale-[0.99]"
                                    onClick={() => ride.driver_id && onDriverClick(ride.driver_id)}
                                >
                                    <div className="relative">
                                        <Avatar className="h-12 w-12 border-2 border-background shadow-sm">
                                            <AvatarFallback className="bg-purple-100 text-purple-700 font-bold text-sm">
                                                {(ride.driver_name ?? "DR").slice(0, 2).toUpperCase()}
                                            </AvatarFallback>
                                        </Avatar>
                                        <div className="absolute -bottom-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-purple-500 ring-2 ring-background">
                                            <Car className="h-3 w-3 text-white" />
                                        </div>
                                    </div>
                                    <div className="flex-1 min-w-0">
                                        <p className="text-[10px] font-bold text-muted-foreground uppercase tracking-wider mb-0.5">Driver</p>
                                        <p className="truncate text-base font-semibold text-foreground">{ride.driver_name ?? "—"}</p>
                                    </div>
                                    <div className="flex items-center gap-1.5">
                                        {ride.driver_phone && (
                                            <Button
                                                size="icon"
                                                variant="outline"
                                                className="h-10 w-10 rounded-full shadow-sm hover:bg-purple-50 hover:text-purple-600 hover:border-purple-200 transition-colors mr-1"
                                                onClick={(e) => {
                                                    e.stopPropagation();
                                                    window.open(`tel:${ride.driver_phone}`);
                                                }}
                                            >
                                                <Phone className="h-4 w-4" />
                                            </Button>
                                        )}
                                        <ChevronRight className="h-5 w-5 text-muted-foreground" />
                                    </div>
                                </div>
                            </>
                        )}
                    </div>
                </div>
            </div>

            {/* Actions (Sticky bottom) */}
            <div className="absolute bottom-0 left-0 right-0 border-t bg-background/95 backdrop-blur-md p-4 shadow-[0_-10px_30px_-15px_rgba(0,0,0,0.1)]">
                <div className="flex gap-3 max-w-sm mx-auto">
                    <Button
                        variant="destructive"
                        className="flex-1 gap-2 h-12 font-bold shadow-sm"
                        onClick={() => onCancelRide(ride.id)}
                    >
                        <XCircle className="h-4 w-4" /> Cancel Ride
                    </Button>
                    {onCompleteRide && (
                        <Button
                            className="flex-1 gap-2 h-12 font-bold bg-green-600 hover:bg-green-700 text-white shadow-sm"
                            onClick={() => {
                                if (window.confirm("Are you sure you want to force-complete this ride?")) {
                                    onCompleteRide(ride.id);
                                }
                            }}
                        >
                            <CheckCircle className="h-4 w-4" /> Complete Ride
                        </Button>
                    )}
                </div>
            </div>
        </div>
    );
}
