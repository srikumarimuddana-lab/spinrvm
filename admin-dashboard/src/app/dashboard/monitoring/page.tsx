// src/app/dashboard/monitoring/page.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { v4 as uuidv4 } from "uuid";
import { useAuthStore } from "@/store/authStore";
import { getMonitoringDrivers, getMonitoringRides, getServiceAreas, getVehicleTypes } from "@/lib/api";
import { useMonitoringSocket } from "@/hooks/use-monitoring-socket";
import { MonitoringMap, MapHandles } from "./monitoring-map";
import { MonitoringToolbar } from "./toolbar";
import { AlertFeed } from "./alert-feed";
import { DriverPanel } from "./driver-panel";
import { RidePanel } from "./ride-panel";
import {
    AlertEvent,
    MonitoringCounts,
    MonitoringDriver,
    MonitoringFilters,
    MonitoringRide,
    MonitoringWsEvent,
    SelectedItem,
} from "./types";

export default function MonitoringPage() {
    const token = useAuthStore((s) => s.token);

    // ── Ref-based data stores (never cause re-renders) ────────────────
    const driversRef = useRef<Map<string, MonitoringDriver>>(new Map());
    const ridesRef = useRef<Map<string, MonitoringRide>>(new Map());
    const mapHandlesRef = useRef<MapHandles | null>(null);

    // ── React state (causes re-renders) ──────────────────────────────
    const [counts, setCounts] = useState<MonitoringCounts>({ online: 0, onRide: 0, offline: 0, activeRides: 0 });
    const [filters, setFilters] = useState<MonitoringFilters>({
        showOnline: true,
        showOffline: false,
        showRides: true,
        serviceAreaId: null,
        vehicleTypeId: null,
    });
    const [searchQuery, setSearchQuery] = useState("");
    const [followMode, setFollowMode] = useState(false);
    const [selected, setSelected] = useState<SelectedItem>(null);
    const [alerts, setAlerts] = useState<AlertEvent[]>([]);
    const [serviceAreas, setServiceAreas] = useState<{ id: string; name: string }[]>([]);
    const [vehicleTypes, setVehicleTypes] = useState<{ id: string; name: string }[]>([]);

    // ── Derived selections ────────────────────────────────────────────
    const selectedDriver =
        selected?.type === "driver" ? driversRef.current.get(selected.id) ?? null : null;
    const selectedRide =
        selected?.type === "ride" ? ridesRef.current.get(selected.id) ?? null : null;

    // ── Count recalculation ───────────────────────────────────────────
    const recalcCounts = useCallback(() => {
        let online = 0, onRide = 0, offline = 0;
        driversRef.current.forEach((d) => {
            if (!d.is_online) offline++;
            else if (d.active_ride_id) onRide++;
            else online++;
        });
        setCounts({ online, onRide, offline, activeRides: ridesRef.current.size });
    }, []);

    // ── Push alert ────────────────────────────────────────────────────
    const pushAlert = useCallback((alert: Omit<AlertEvent, "id" | "timestamp">) => {
        setAlerts((prev) =>
            [{ ...alert, id: uuidv4(), timestamp: new Date().toISOString() }, ...prev].slice(0, 200)
        );
    }, []);

    // ── Initial data load ─────────────────────────────────────────────
    useEffect(() => {
        async function load() {
            const [drivers, rides, areas, vtypes] = await Promise.allSettled([
                getMonitoringDrivers(),
                getMonitoringRides(),
                getServiceAreas(),
                getVehicleTypes(),
            ]);

            if (drivers.status === "fulfilled") {
                drivers.value.forEach((d) => driversRef.current.set(d.id, d));
            }
            if (rides.status === "fulfilled") {
                rides.value.forEach((r) => ridesRef.current.set(r.id, r));
            }
            if (areas.status === "fulfilled") {
                setServiceAreas(areas.value.map((a: any) => ({ id: a.id, name: a.name })));
            }
            if (vtypes.status === "fulfilled") {
                setVehicleTypes(vtypes.value.map((v: any) => ({ id: v.id, name: v.name })));
            }
            recalcCounts();
        }
        load();
    }, [recalcCounts]);

    // ── WebSocket event handler ───────────────────────────────────────
    const handleWsEvent = useCallback(
        (event: MonitoringWsEvent) => {
            switch (event.type) {
                case "driver_location_update": {
                    const d = driversRef.current.get(event.driver_id);
                    if (d) {
                        const updated = { ...d, lat: event.lat, lng: event.lng };
                        driversRef.current.set(event.driver_id, updated);
                        mapHandlesRef.current?.updateDriverMarker(updated);
                        if (followMode && selected?.type === "driver" && selected.id === event.driver_id) {
                            mapHandlesRef.current?.panTo(event.lat, event.lng);
                        }
                    }
                    break;
                }
                case "driver_status_changed": {
                    const d = driversRef.current.get(event.driver_id);
                    if (d) {
                        const updated = { ...d, is_online: event.is_online };
                        driversRef.current.set(event.driver_id, updated);
                        mapHandlesRef.current?.updateDriverMarker(updated);
                        recalcCounts();
                        pushAlert({
                            icon: event.is_online ? "online" : "offline",
                            message: `Driver ${d.name} went ${event.is_online ? "online" : "offline"}`,
                            driver_id: event.driver_id,
                        });
                    }
                    break;
                }
                case "ride_status_changed": {
                    const r = ridesRef.current.get(event.ride_id);
                    if (r) {
                        const updated = { ...r, status: event.status as MonitoringRide["status"] };
                        ridesRef.current.set(event.ride_id, updated);
                        mapHandlesRef.current?.updateRideMarkers(updated);
                        if (event.status === "completed" || event.status === "cancelled") {
                            ridesRef.current.delete(event.ride_id);
                            mapHandlesRef.current?.removeRideMarkers(event.ride_id);
                            if (selected?.type === "ride" && selected.id === event.ride_id) {
                                setSelected(null);
                            }
                            recalcCounts();
                            pushAlert({
                                icon: event.status === "completed" ? "ride_done" : "ride_cancelled",
                                message: `Ride #${event.ride_id.slice(-8)} ${event.status}${r.total_fare ? ` — $${r.total_fare.toFixed(2)}` : ""}`,
                                ride_id: event.ride_id,
                            });
                        }
                    }
                    break;
                }
                case "ride_requested": {
                    ridesRef.current.set(event.ride.id, event.ride);
                    mapHandlesRef.current?.updateRideMarkers(event.ride);
                    recalcCounts();
                    pushAlert({
                        icon: "ride_new",
                        message: `New ride #${event.ride.id.slice(-8)} requested`,
                        ride_id: event.ride.id,
                    });
                    break;
                }
            }
        },
        [followMode, selected, recalcCounts, pushAlert]
    );

    const { status: wsStatus } = useMonitoringSocket({ token, onEvent: handleWsEvent });

    // ── Selection helpers ─────────────────────────────────────────────
    const selectDriver = useCallback(
        (id: string) => {
            setSelected({ type: "driver", id });
            const d = driversRef.current.get(id);
            if (d?.lat && d.lng) mapHandlesRef.current?.panTo(d.lat, d.lng);
        },
        []
    );

    const selectRide = useCallback(
        (id: string) => {
            setSelected({ type: "ride", id });
            const r = ridesRef.current.get(id);
            if (r) mapHandlesRef.current?.panTo(r.pickup_lat, r.pickup_lng);
        },
        []
    );

    const handleAlertClick = useCallback(
        (evt: AlertEvent) => {
            if (evt.driver_id) selectDriver(evt.driver_id);
            else if (evt.ride_id) selectRide(evt.ride_id);
        },
        [selectDriver, selectRide]
    );

    return (
        <div className="flex h-screen flex-col overflow-hidden">
            <MonitoringToolbar
                counts={counts}
                filters={filters}
                onFilterChange={(f) => setFilters((prev) => ({ ...prev, ...f }))}
                searchQuery={searchQuery}
                onSearchChange={setSearchQuery}
                followMode={followMode}
                onFollowToggle={() => setFollowMode((v) => !v)}
                serviceAreas={serviceAreas}
                vehicleTypes={vehicleTypes}
                wsStatus={wsStatus}
            />

            <div className="flex flex-1 overflow-hidden">
                {/* Map */}
                <div className="relative flex-1">
                    <MonitoringMap
                        driversMap={driversRef}
                        ridesMap={ridesRef}
                        filters={filters}
                        searchQuery={searchQuery}
                        selected={selected}
                        followMode={followMode}
                        onSelectDriver={selectDriver}
                        onSelectRide={selectRide}
                        onReady={(handles) => { mapHandlesRef.current = handles; }}
                    />
                </div>

                {/* Detail panel */}
                <div className="w-80 shrink-0 overflow-hidden border-l border-border">
                    {selectedDriver ? (
                        <DriverPanel
                            driver={selectedDriver}
                            onRideClick={selectRide}
                        />
                    ) : selectedRide ? (
                        <RidePanel
                            ride={selectedRide}
                            onDriverClick={selectDriver}
                            onCancelRide={(id) => {
                                console.log("Cancel ride", id);
                            }}
                        />
                    ) : (
                        <div className="flex h-full flex-col items-center justify-center gap-2 p-8 text-center">
                            <div className="rounded-full bg-muted p-4">
                                <span className="text-2xl">🗺️</span>
                            </div>
                            <p className="font-medium">Select a driver or ride</p>
                            <p className="text-xs text-muted-foreground">
                                Click any marker on the map to see details here
                            </p>
                        </div>
                    )}
                </div>
            </div>

            <AlertFeed
                events={alerts}
                onClear={() => setAlerts([])}
                onEventClick={handleAlertClick}
            />
        </div>
    );
}
