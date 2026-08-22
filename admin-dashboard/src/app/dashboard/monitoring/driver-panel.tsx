// src/app/dashboard/monitoring/driver-panel.tsx
"use client";

import { useEffect, useState } from "react";
import { MonitoringDriver } from "./types";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/use-toast";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { ExternalLink, Flag, Phone, Star } from "lucide-react";
import Link from "next/link";
import { getDriverRides, getDriverDocuments } from "@/lib/api";

interface DriverPanelProps {
    driver: MonitoringDriver;
    onRideClick: (rideId: string) => void;
}

export function DriverPanel({ driver, onRideClick }: DriverPanelProps) {
    const { toast } = useToast();
    const [rides, setRides] = useState<any[]>([]);
    const [docs, setDocs] = useState<any[]>([]);
    const [tabLoading, setTabLoading] = useState(false);

    useEffect(() => {
        setTabLoading(true);
        Promise.all([
            getDriverRides(driver.id),
            getDriverDocuments(driver.id),
        ]).then(([ridesRes, docsRes]) => {
            setRides((ridesRes as any)?.rides?.slice(0, 10) ?? []);
            setDocs(Array.isArray(docsRes) ? docsRes : []);
        }).catch(() => {}).finally(() => setTabLoading(false));
    }, [driver.id]);

    const initials = driver.name
        .split(" ")
        .map((n) => n[0])
        .join("")
        .toUpperCase()
        .slice(0, 2);

    // Stale = driver tapped "Go Online" but their app hasn't pinged us within
    // the presence TTL (~90 s). Effective is_online is already false here, so
    // the main badge shows Offline — this pill just explains *why* to the operator.
    const isStale = driver.intent_online === true && driver.is_present === false;

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
                <div className="flex flex-col items-end gap-1">
                    <Badge
                        variant={driver.is_online ? "default" : "secondary"}
                        // eslint-disable-next-line no-restricted-syntax -- solid-fill white-text badge; dark-mode --success (2.02:1) fails WCAG AA against white text, see #2816 Batch 1 finding
                        className={driver.is_online ? "bg-green-500 hover:bg-green-500" : ""}
                    >
                        {driver.is_online ? (driver.active_ride_id ? "On Ride" : "Online") : "Offline"}
                    </Badge>
                    {isStale && (
                        <Badge
                            variant="outline"
                            className="border-warning/50 text-[10px] text-warning"
                            title="Driver tapped Go Online but their app is not reachable (no heartbeat within 90s)."
                        >
                            Stale
                        </Badge>
                    )}
                </div>
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
                                {/* eslint-disable-next-line no-restricted-syntax -- standard star-rating amber convention, not a status signal (#2816) */}
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
                            <p className={`text-sm font-bold ${driver.is_online ? "text-success" : "text-muted-foreground"}`}>
                                {driver.is_online ? (driver.active_ride_id ? "On Ride" : "Online") : "Offline"}
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
                                // eslint-disable-next-line no-restricted-syntax -- decorative link-card accent, not a status signal (#2816)
                                className="mt-1 w-full rounded-lg border border-blue-500/30 bg-blue-500/5 px-3 py-2 text-left text-xs text-blue-600 dark:text-blue-400 hover:bg-blue-500/10"
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
                            onClick={() => toast({ title: "Use driver profile to flag", description: `Open the full profile for ${driver.name} to submit a formal flag.` })}
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
                    {tabLoading ? (
                        <div className="flex justify-center py-8"><span className="text-xs text-muted-foreground">Loading…</span></div>
                    ) : rides.length === 0 ? (
                        <p className="pt-6 text-center text-xs text-muted-foreground">No rides found</p>
                    ) : (
                        <div className="mt-2 space-y-1">
                            {rides.map((r: any) => (
                                <div key={r.id} className="rounded-lg border border-border px-3 py-2 text-xs">
                                    <div className="flex items-center justify-between">
                                        <span className="font-medium">{r.pickup_address?.split(',')[0] ?? 'Pickup'} → {r.dropoff_address?.split(',')[0] ?? 'Dropoff'}</span>
                                        <span className={`rounded-full px-1.5 py-0.5 text-[10px] font-semibold ${r.status === 'completed' ? 'bg-success/15 text-success' : r.status === 'cancelled' ? 'bg-destructive/15 text-destructive' : 'bg-warning/15 text-warning'}`}>{r.status}</span>
                                    </div>
                                    <div className="mt-0.5 flex items-center justify-between text-muted-foreground">
                                        <span>{r.created_at ? new Date(r.created_at).toLocaleDateString('en-CA') : '—'}</span>
                                        <span>${r.total_fare ? Number(r.total_fare).toFixed(2) : '—'}</span>
                                    </div>
                                </div>
                            ))}
                        </div>
                    )}
                </TabsContent>

                {/* Documents tab */}
                <TabsContent value="documents" className="flex-1 overflow-y-auto px-4 pb-4">
                    {tabLoading ? (
                        <div className="flex justify-center py-8"><span className="text-xs text-muted-foreground">Loading…</span></div>
                    ) : docs.length === 0 ? (
                        <p className="pt-6 text-center text-xs text-muted-foreground">No documents found</p>
                    ) : (
                        <div className="mt-2 space-y-2">
                            {docs.map((doc: any) => (
                                <div key={doc.id} className="flex items-center justify-between rounded-lg border border-border px-3 py-2 text-xs">
                                    <span className="font-medium capitalize">{(doc.document_type ?? 'Document').replace(/_/g, ' ')}</span>
                                    <span className={`rounded-full px-2 py-0.5 text-[10px] font-semibold ${doc.status === 'verified' ? 'bg-success/15 text-success' : doc.status === 'expired' ? 'bg-destructive/15 text-destructive' : 'bg-warning/15 text-warning'}`}>{doc.status ?? 'pending'}</span>
                                </div>
                            ))}
                        </div>
                    )}
                </TabsContent>
            </Tabs>
        </div>
    );
}
