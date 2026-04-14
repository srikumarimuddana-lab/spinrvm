// src/app/dashboard/monitoring/monitoring-map.tsx
"use client";

import { useCallback, useEffect, useRef } from "react";
import { GoogleMap, useJsApiLoader } from "@react-google-maps/api";
import { MonitoringDriver, MonitoringFilters, MonitoringRide, SelectedItem } from "./types";

const MAP_CONTAINER_STYLE = { width: "100%", height: "100%" };
const DEFAULT_CENTER = { lat: 43.6532, lng: -79.3832 }; // Toronto — update to your service area
const DEFAULT_ZOOM = 12;

// Colour tokens for driver markers
const DRIVER_COLOURS = {
    online_free: "#22c55e",   // green-500
    online_ride: "#f59e0b",   // amber-500
    offline: "#9ca3af",       // gray-400
};

function makeDriverIcon(driver: MonitoringDriver): google.maps.Symbol {
    let fill: string;
    if (!driver.is_online) fill = DRIVER_COLOURS.offline;
    else if (driver.active_ride_id) fill = DRIVER_COLOURS.online_ride;
    else fill = DRIVER_COLOURS.online_free;

    return {
        path: google.maps.SymbolPath.CIRCLE,
        fillColor: fill,
        fillOpacity: 1,
        strokeColor: "#ffffff",
        strokeWeight: 2,
        scale: 9,
    };
}

interface MonitoringMapProps {
    driversMap: React.MutableRefObject<Map<string, MonitoringDriver>>;
    ridesMap: React.MutableRefObject<Map<string, MonitoringRide>>;
    filters: MonitoringFilters;
    searchQuery: string;
    selected: SelectedItem;
    followMode: boolean;
    onSelectDriver: (id: string) => void;
    onSelectRide: (id: string) => void;
    /** Exposes imperative update handles to parent */
    onReady: (handles: MapHandles) => void;
}

export interface MapHandles {
    updateDriverMarker: (driver: MonitoringDriver) => void;
    removeDriverMarker: (driverId: string) => void;
    updateRideMarkers: (ride: MonitoringRide) => void;
    removeRideMarkers: (rideId: string) => void;
    panTo: (lat: number, lng: number) => void;
}

export function MonitoringMap({
    driversMap,
    ridesMap,
    filters,
    searchQuery,
    selected,
    followMode,
    onSelectDriver,
    onSelectRide,
    onReady,
}: MonitoringMapProps) {
    const apiKey = process.env.NEXT_PUBLIC_GOOGLE_MAPS_API_KEY ?? "";
    const { isLoaded, loadError } = useJsApiLoader({
        googleMapsApiKey: apiKey,
        id: "google-map-monitoring",
    });

    const mapRef = useRef<google.maps.Map | null>(null);
    const driverMarkersRef = useRef<Map<string, google.maps.Marker>>(new Map());
    const rideMarkersRef = useRef<
        Map<string, { pickup: google.maps.Marker; dropoff: google.maps.Marker; line: google.maps.Polyline | null }>
    >(new Map());

    const onSelectDriverRef = useRef(onSelectDriver);
    onSelectDriverRef.current = onSelectDriver;
    const onSelectRideRef = useRef(onSelectRide);
    onSelectRideRef.current = onSelectRide;

    // ── Imperative driver marker management ──────────────────────────
    const updateDriverMarker = useCallback((driver: MonitoringDriver) => {
        if (!mapRef.current || !driver.lat || !driver.lng) return;

        const visible =
            searchQuery
                ? driver.name.toLowerCase().includes(searchQuery.toLowerCase())
                : driver.is_online
                ? filters.showOnline
                : filters.showOffline;

        let marker = driverMarkersRef.current.get(driver.id);
        const position = { lat: driver.lat, lng: driver.lng };

        if (!marker) {
            marker = new google.maps.Marker({
                map: mapRef.current,
                position,
                icon: makeDriverIcon(driver),
                title: driver.name,
                visible,
                label: {
                    text: driver.name.slice(0, 1).toUpperCase(),
                    color: "#fff",
                    fontSize: "11px",
                    fontWeight: "bold",
                },
            });
            marker.addListener("click", () => onSelectDriverRef.current(driver.id));
            driverMarkersRef.current.set(driver.id, marker);
        } else {
            marker.setPosition(position);
            marker.setIcon(makeDriverIcon(driver));
            marker.setVisible(visible);
        }
    }, [filters, searchQuery]);

    const removeDriverMarker = useCallback((driverId: string) => {
        const m = driverMarkersRef.current.get(driverId);
        if (m) { m.setMap(null); driverMarkersRef.current.delete(driverId); }
    }, []);

    // ── Imperative ride marker management ────────────────────────────
    const updateRideMarkers = useCallback((ride: MonitoringRide) => {
        if (!mapRef.current) return;
        if (!filters.showRides) {
            const existing = rideMarkersRef.current.get(ride.id);
            if (existing) {
                existing.pickup.setVisible(false);
                existing.dropoff.setVisible(false);
                existing.line?.setVisible(false);
            }
            return;
        }

        let entry = rideMarkersRef.current.get(ride.id);
        if (!entry) {
            const pickup = new google.maps.Marker({
                map: mapRef.current,
                position: { lat: ride.pickup_lat, lng: ride.pickup_lng },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: "#3b82f6",
                    fillOpacity: 1,
                    strokeColor: "#fff",
                    strokeWeight: 2,
                    scale: 8,
                },
                label: { text: "P", color: "#fff", fontSize: "10px", fontWeight: "bold" },
                title: `Pickup: ${ride.pickup_address ?? ""}`,
            });
            pickup.addListener("click", () => onSelectRideRef.current(ride.id));

            const dropoff = new google.maps.Marker({
                map: mapRef.current,
                position: { lat: ride.dropoff_lat, lng: ride.dropoff_lng },
                icon: {
                    path: google.maps.SymbolPath.CIRCLE,
                    fillColor: "#ef4444",
                    fillOpacity: 1,
                    strokeColor: "#fff",
                    strokeWeight: 2,
                    scale: 8,
                },
                label: { text: "D", color: "#fff", fontSize: "10px", fontWeight: "bold" },
                title: `Dropoff: ${ride.dropoff_address ?? ""}`,
            });
            dropoff.addListener("click", () => onSelectRideRef.current(ride.id));

            const line = new google.maps.Polyline({
                map: mapRef.current,
                path: [
                    { lat: ride.pickup_lat, lng: ride.pickup_lng },
                    { lat: ride.dropoff_lat, lng: ride.dropoff_lng },
                ],
                strokeColor: "#3b82f6",
                strokeOpacity: 0.5,
                strokeWeight: 2,
                icons: [{ icon: { path: "M 0,-1 0,1", strokeOpacity: 1, scale: 3 }, offset: "0", repeat: "16px" }],
            });

            entry = { pickup, dropoff, line };
            rideMarkersRef.current.set(ride.id, entry);
        } else {
            entry.pickup.setPosition({ lat: ride.pickup_lat, lng: ride.pickup_lng });
            entry.dropoff.setPosition({ lat: ride.dropoff_lat, lng: ride.dropoff_lng });
            entry.pickup.setVisible(true);
            entry.dropoff.setVisible(true);
            entry.line?.setVisible(true);
        }
    }, [filters.showRides]);

    const removeRideMarkers = useCallback((rideId: string) => {
        const entry = rideMarkersRef.current.get(rideId);
        if (entry) {
            entry.pickup.setMap(null);
            entry.dropoff.setMap(null);
            entry.line?.setMap(null);
            rideMarkersRef.current.delete(rideId);
        }
    }, []);

    const panTo = useCallback((lat: number, lng: number) => {
        mapRef.current?.panTo({ lat, lng });
    }, []);

    const onMapLoad = useCallback(
        (map: google.maps.Map) => {
            mapRef.current = map;
            driversMap.current.forEach(updateDriverMarker);
            ridesMap.current.forEach(updateRideMarkers);
            onReady({ updateDriverMarker, removeDriverMarker, updateRideMarkers, removeRideMarkers, panTo });
        },
        [driversMap, ridesMap, updateDriverMarker, updateRideMarkers, removeDriverMarker, removeRideMarkers, panTo, onReady]
    );

    // Re-apply filter visibility when filters change
    useEffect(() => {
        driverMarkersRef.current.forEach((marker, id) => {
            const d = driversMap.current.get(id);
            if (!d) return;
            const vis = d.is_online ? filters.showOnline : filters.showOffline;
            marker.setVisible(vis);
        });
        rideMarkersRef.current.forEach(({ pickup, dropoff, line }) => {
            pickup.setVisible(filters.showRides);
            dropoff.setVisible(filters.showRides);
            line?.setVisible(filters.showRides);
        });
    }, [filters, driversMap]);

    // Follow selected driver
    useEffect(() => {
        if (!followMode || !selected || selected.type !== "driver") return;
        const d = driversMap.current.get(selected.id);
        if (d?.lat && d.lng) panTo(d.lat, d.lng);
    });

    if (loadError) {
        return (
            <div className="flex h-full items-center justify-center bg-muted">
                <p className="text-sm text-destructive">
                    Failed to load Google Maps. Check NEXT_PUBLIC_GOOGLE_MAPS_API_KEY.
                </p>
            </div>
        );
    }

    if (!isLoaded) {
        return (
            <div className="flex h-full items-center justify-center bg-muted">
                <p className="text-sm text-muted-foreground">Loading map…</p>
            </div>
        );
    }

    if (!apiKey) {
        return (
            <div className="flex h-full flex-col items-center justify-center gap-2 bg-muted">
                <p className="text-sm font-medium">Google Maps API key not set</p>
                <p className="text-xs text-muted-foreground">
                    Add NEXT_PUBLIC_GOOGLE_MAPS_API_KEY to admin-dashboard/.env.local and restart.
                </p>
            </div>
        );
    }

    return (
        <GoogleMap
            mapContainerStyle={MAP_CONTAINER_STYLE}
            center={DEFAULT_CENTER}
            zoom={DEFAULT_ZOOM}
            onLoad={onMapLoad}
            options={{
                disableDefaultUI: false,
                zoomControl: true,
                streetViewControl: false,
                mapTypeControl: false,
                fullscreenControl: true,
            }}
        />
    );
}
