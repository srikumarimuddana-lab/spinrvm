// src/app/dashboard/monitoring/driver-panel.tsx
"use client";

import { MonitoringDriver } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ExternalLink, Flag, Phone, Star } from "lucide-react";
import Link from "next/link";

interface DriverPanelProps {
    driver: MonitoringDriver;
    onRideClick: (rideId: string) => void;
}

export function DriverPanel({ driver, onRideClick }: DriverPanelProps) {
    const initials = driver.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);

    return (
        <div className="flex h-full flex-col overflow-hidden">
            {/* Header */}
            <div className="flex items-center gap-3 border-b border-border p-4">
                <Avatar className="h-12 w-12">
                    <AvatarImage src={driver.photo_url ?? undefined} />
                    <AvatarFallback>{initials}</AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                    <p className="truncate font-semibold">{driver.name}</p>
                    <p className="text-xs text-muted-foreground">{driver.phone}</p>
                </div>
                <Badge
                    variant={driver.is_online ? "default" : "secondary"}
                    className={driver.is_online ? "bg-green-500 hover:bg-green-500" : ""}
                >
                    {driver.is_online ? (driver.active_ride_id ? "On Ride" : "Online") : "Offline"}
                </Badge>
            </div>

            <Tabs defaultValue="overview" className="flex flex-1 flex-col overflow-hidden">
                <TabsList className="mx-4 mt-2 w-auto justify-start">
                    <TabsTrigger value="overview" className="text-xs">Overview</TabsTrigger>
                    <TabsTrigger value="rides" className="text-xs">Rides</TabsTrigger>
                    <TabsTrigger value="documents" className="text-xs">Docs</TabsTrigger>
                </TabsList>

                {/* Overview tab */}
                <TabsContent value="overview" className="flex-1 overflow-y-auto px-4 pb-4">
                    {/* Rating + stats */}
                    <div className="mt-3 grid grid-cols-3 gap-2">
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <div className="flex items-center justify-center gap-1 text-lg font-bold">
                                <Star className="h-4 w-4 fill-amber-400 text-amber-400" />
                                {driver.rating?.toFixed(1) ?? "—"}
                            </div>
                            <p className="text-xs text-muted-foreground">Rating</p>
                        </div>
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <p className="text-lg font-bold">{driver.total_rides}</p>
                            <p className="text-xs text-muted-foreground">Rides</p>
                        </div>
                        <div className="rounded-lg bg-muted p-2 text-center">
                            <p className="text-lg font-bold text-green-600">
                                {driver.is_online ? "●" : "○"}
                            </p>
                            <p className="text-xs text-muted-foreground">Status</p>
                        </div>
                    </div>

                    {/* Vehicle info */}
                    <div className="mt-4 space-y-2">
                        <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                            Vehicle
                        </p>
                        <div className="rounded-lg border border-border p-3 text-sm">
                            <p className="font-medium">
                                {[driver.vehicle_make, driver.vehicle_model].filter(Boolean).join(" ") || "—"}
                            </p>
                            {driver.vehicle_color && (
                                <p className="text-xs text-muted-foreground">{driver.vehicle_color}</p>
                            )}
                            {driver.license_plate && (
                                <p className="mt-1 font-mono text-xs font-bold uppercase tracking-widest">
                                    {driver.license_plate}
                                </p>
                            )}
                        </div>
                    </div>

                    {/* Current ride */}
                    {driver.active_ride_id && (
                        <div className="mt-4">
                            <p className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                                Current Ride
                            </p>
                            <button
                                onClick={() => onRideClick(driver.active_ride_id!)}
                                className="mt-1 w-full rounded-lg border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-left text-xs text-blue-600 hover:bg-blue-500/10"
                            >
                                Ride #{driver.active_ride_id.slice(-8)} →
                            </button>
                        </div>
                    )}

                    {/* Actions */}
                    <div className="mt-4 flex gap-2">
                        <Button
                            size="sm"
                            variant="outline"
                            className="flex-1 gap-1.5 text-xs"
                            onClick={() => window.open(`tel:${driver.phone}`)}
                        >
                            <Phone className="h-3.5 w-3.5" /> Contact
                        </Button>
                        <Button
                            size="sm"
                            variant="outline"
                            className="flex-1 gap-1.5 text-xs text-destructive hover:text-destructive"
                        >
                            <Flag className="h-3.5 w-3.5" /> Flag
                        </Button>
                    </div>
                    <Link href={`/dashboard/drivers?id=${driver.id}`}>
                        <Button
                            size="sm"
                            variant="ghost"
                            className="mt-2 w-full gap-1.5 text-xs"
                        >
                            <ExternalLink className="h-3.5 w-3.5" /> View Full Profile
                        </Button>
                    </Link>
                </TabsContent>

                {/* Rides tab */}
                <TabsContent value="rides" className="flex-1 overflow-y-auto px-4 pb-4">
                    <div className="mt-4">
                        <Link href={`/dashboard/drivers?id=${driver.id}`}>
                            <Button size="sm" variant="outline" className="w-full text-xs">
                                View ride history in Drivers page
                            </Button>
                        </Link>
                    </div>
                </TabsContent>

                {/* Documents tab */}
                <TabsContent value="documents" className="flex-1 overflow-y-auto px-4 pb-4">
                    <div className="mt-4">
                        <Link href={`/dashboard/documents?driver_id=${driver.id}`}>
                            <Button size="sm" variant="outline" className="w-full text-xs">
                                View Documents
                            </Button>
                        </Link>
                    </div>
                </TabsContent>
            </Tabs>
        </div>
    );
}
