// src/app/dashboard/monitoring/monitoring-map.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { MonitoringDriver, MonitoringFilters, MonitoringRide, SelectedItem } from "./types";

// OpenFreeMap — free, no API key, no attribution fees. Switch the style
// URL if you want a different look (liberty / positron / bright / dark).
const MAP_STYLE_URL = "https://tiles.openfreemap.org/styles/liberty";
// MapLibre uses [lng, lat] ordering everywhere, unlike Google Maps.
const DEFAULT_CENTER: [number, number] = [-79.3832, 43.6532]; // Toronto — update to your service area
const DEFAULT_ZOOM = 12;

// Colour tokens for driver markers
const DRIVER_COLOURS = {
    online_free: "#22c55e",   // green-500
    online_ride: "#f59e0b",   // amber-500
    offline: "#9ca3af",       // gray-400
};

function driverColor(driver: MonitoringDriver): string {
    if (!driver.is_online) return DRIVER_COLOURS.offline;
    if (driver.active_ride_id) return DRIVER_COLOURS.online_ride;
    return DRIVER_COLOURS.online_free;
}

function makeDriverMarkerEl(driver: MonitoringDriver): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "spinr-driver-marker";
    el.style.width = "22px";
    el.style.height = "22px";
    el.style.borderRadius = "50%";
    el.style.backgroundColor = driverColor(driver);
    el.style.border = "2px solid #ffffff";
    el.style.boxShadow = "0 0 3px rgba(0,0,0,0.3)";
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.color = "#ffffff";
    el.style.fontSize = "11px";
    el.style.fontWeight = "bold";
    el.style.cursor = "pointer";
    el.textContent = driver.name.slice(0, 1).toUpperCase();
    el.title = driver.name;
    return el;
}

function makeRidePointMarkerEl(kind: "P" | "D", title: string): HTMLDivElement {
    const el = document.createElement("div");
    el.className = "spinr-ride-marker";
    const bg = kind === "P" ? "#3b82f6" : "#ef4444";
    el.style.width = "20px";
    el.style.height = "20px";
    el.style.borderRadius = "50%";
    el.style.backgroundColor = bg;
    el.style.border = "2px solid #ffffff";
    el.style.boxShadow = "0 0 3px rgba(0,0,0,0.3)";
    el.style.display = "flex";
    el.style.alignItems = "center";
    el.style.justifyContent = "center";
    el.style.color = "#ffffff";
    el.style.fontSize = "10px";
    el.style.fontWeight = "bold";
    el.style.cursor = "pointer";
    el.textContent = kind;
    el.title = title;
    return el;
}

const MAP_CONTAINER_STYLE: React.CSSProperties = { width: "100%", height: "100%" };

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

function rideLineSourceId(rideId: string): string {
    return `ride-line-src-${rideId}`;
}
function rideLineLayerId(rideId: string): string {
    return `ride-line-lyr-${rideId}`;
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
    const containerRef = useRef<HTMLDivElement | null>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const [isLoaded, setIsLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    // Driver markers — key: driver.id. MapLibre Markers are DOM-backed.
    const driverMarkersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
    // Visibility tracked separately because MapLibre markers don't have a
    // setVisible() — we add/remove them from the map instead.
    const driverVisibleRef = useRef<Map<string, boolean>>(new Map());

    // Ride markers + line source/layer ids
    const rideMarkersRef = useRef<
        Map<string, { pickup: maplibregl.Marker; dropoff: maplibregl.Marker; sourceId: string; layerId: string }>
    >(new Map());
    const rideVisibleRef = useRef<Map<string, boolean>>(new Map());

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
        const lngLat: [number, number] = [driver.lng, driver.lat];

        if (!marker) {
            const el = makeDriverMarkerEl(driver);
            el.addEventListener("click", () => onSelectDriverRef.current(driver.id));
            marker = new maplibregl.Marker({ element: el }).setLngLat(lngLat);
            if (visible) marker.addTo(mapRef.current);
            driverMarkersRef.current.set(driver.id, marker);
            driverVisibleRef.current.set(driver.id, visible);
        } else {
            marker.setLngLat(lngLat);
            // Refresh colour + initial letter in case status changed
            const el = marker.getElement();
            el.style.backgroundColor = driverColor(driver);
            el.title = driver.name;
            el.textContent = driver.name.slice(0, 1).toUpperCase();

            const wasVisible = driverVisibleRef.current.get(driver.id) ?? false;
            if (visible && !wasVisible) marker.addTo(mapRef.current);
            else if (!visible && wasVisible) marker.remove();
            driverVisibleRef.current.set(driver.id, visible);
        }
    }, [filters, searchQuery]);

    const removeDriverMarker = useCallback((driverId: string) => {
        const m = driverMarkersRef.current.get(driverId);
        if (m) {
            m.remove();
            driverMarkersRef.current.delete(driverId);
            driverVisibleRef.current.delete(driverId);
        }
    }, []);

    // ── Imperative ride marker management ────────────────────────────
    const setRideVisible = useCallback((rideId: string, visible: boolean) => {
        const entry = rideMarkersRef.current.get(rideId);
        if (!entry || !mapRef.current) return;
        const wasVisible = rideVisibleRef.current.get(rideId) ?? false;
        if (visible && !wasVisible) {
            entry.pickup.addTo(mapRef.current);
            entry.dropoff.addTo(mapRef.current);
            if (mapRef.current.getLayer(entry.layerId)) {
                mapRef.current.setLayoutProperty(entry.layerId, "visibility", "visible");
            }
        } else if (!visible && wasVisible) {
            entry.pickup.remove();
            entry.dropoff.remove();
            if (mapRef.current.getLayer(entry.layerId)) {
                mapRef.current.setLayoutProperty(entry.layerId, "visibility", "none");
            }
        }
        rideVisibleRef.current.set(rideId, visible);
    }, []);

    const updateRideMarkers = useCallback((ride: MonitoringRide) => {
        if (!mapRef.current) return;

        // Ensure coords are present
        if (
            ride.pickup_lat == null || ride.pickup_lng == null ||
            ride.dropoff_lat == null || ride.dropoff_lng == null
        ) return;

        let entry = rideMarkersRef.current.get(ride.id);
        const pickupLngLat: [number, number] = [ride.pickup_lng, ride.pickup_lat];
        const dropoffLngLat: [number, number] = [ride.dropoff_lng, ride.dropoff_lat];

        if (!entry) {
            const pickupEl = makeRidePointMarkerEl("P", `Pickup: ${ride.pickup_address ?? ""}`);
            pickupEl.addEventListener("click", () => onSelectRideRef.current(ride.id));
            const pickup = new maplibregl.Marker({ element: pickupEl }).setLngLat(pickupLngLat);

            const dropoffEl = makeRidePointMarkerEl("D", `Dropoff: ${ride.dropoff_address ?? ""}`);
            dropoffEl.addEventListener("click", () => onSelectRideRef.current(ride.id));
            const dropoff = new maplibregl.Marker({ element: dropoffEl }).setLngLat(dropoffLngLat);

            const sourceId = rideLineSourceId(ride.id);
            const layerId = rideLineLayerId(ride.id);

            mapRef.current.addSource(sourceId, {
                type: "geojson",
                data: {
                    type: "Feature",
                    properties: {},
                    geometry: {
                        type: "LineString",
                        coordinates: [pickupLngLat, dropoffLngLat],
                    },
                },
            });
            mapRef.current.addLayer({
                id: layerId,
                type: "line",
                source: sourceId,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": "#3b82f6",
                    "line-width": 2,
                    "line-opacity": 0.5,
                    "line-dasharray": [2, 2],
                },
            });

            entry = { pickup, dropoff, sourceId, layerId };
            rideMarkersRef.current.set(ride.id, entry);
            rideVisibleRef.current.set(ride.id, false);

            if (filters.showRides) {
                setRideVisible(ride.id, true);
            } else {
                // Still hide the layer explicitly
                mapRef.current.setLayoutProperty(layerId, "visibility", "none");
            }
        } else {
            // Update positions
            entry.pickup.setLngLat(pickupLngLat);
            entry.dropoff.setLngLat(dropoffLngLat);
            const src = mapRef.current.getSource(entry.sourceId);
            if (src && "setData" in src) {
                (src as maplibregl.GeoJSONSource).setData({
                    type: "Feature",
                    properties: {},
                    geometry: {
                        type: "LineString",
                        coordinates: [pickupLngLat, dropoffLngLat],
                    },
                });
            }
            setRideVisible(ride.id, filters.showRides);
        }
    }, [filters.showRides, setRideVisible]);

    const removeRideMarkers = useCallback((rideId: string) => {
        const entry = rideMarkersRef.current.get(rideId);
        if (!entry || !mapRef.current) return;
        entry.pickup.remove();
        entry.dropoff.remove();
        if (mapRef.current.getLayer(entry.layerId)) mapRef.current.removeLayer(entry.layerId);
        if (mapRef.current.getSource(entry.sourceId)) mapRef.current.removeSource(entry.sourceId);
        rideMarkersRef.current.delete(rideId);
        rideVisibleRef.current.delete(rideId);
    }, []);

    const panTo = useCallback((lat: number, lng: number) => {
        mapRef.current?.panTo([lng, lat]);
    }, []);

    // ── Map lifecycle ────────────────────────────────────────────────
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        let cancelled = false;
        try {
            const map = new maplibregl.Map({
                container: containerRef.current,
                style: MAP_STYLE_URL,
                center: DEFAULT_CENTER,
                zoom: DEFAULT_ZOOM,
                attributionControl: { compact: true },
            });
            mapRef.current = map;

            map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "top-right");
            map.addControl(new maplibregl.FullscreenControl(), "top-right");

            map.on("load", () => {
                if (cancelled) return;
                setIsLoaded(true);
                driversMap.current.forEach(updateDriverMarker);
                ridesMap.current.forEach(updateRideMarkers);
                onReady({
                    updateDriverMarker,
                    removeDriverMarker,
                    updateRideMarkers,
                    removeRideMarkers,
                    panTo,
                });
            });
            map.on("error", (e) => {
                if (cancelled) return;
                // Tile failures surface here; only surface a hard error for
                // style load failures (missing internet, bad style URL).
                const err = e?.error as Error | undefined;
                if (err && /style/i.test(err.message ?? "")) {
                    setLoadError(err.message);
                }
            });
        } catch (err: unknown) {
            setLoadError(err instanceof Error ? err.message : String(err));
        }

        return () => {
            cancelled = true;
            driverMarkersRef.current.forEach((m) => m.remove());
            driverMarkersRef.current.clear();
            driverVisibleRef.current.clear();
            rideMarkersRef.current.forEach((r) => {
                r.pickup.remove();
                r.dropoff.remove();
            });
            rideMarkersRef.current.clear();
            rideVisibleRef.current.clear();
            mapRef.current?.remove();
            mapRef.current = null;
        };
        // onReady and the imperative callbacks are stable enough via refs;
        // we explicitly do NOT want this effect to re-run when they change.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Re-apply filter visibility when filters change
    useEffect(() => {
        if (!isLoaded) return;
        driverMarkersRef.current.forEach((_marker, id) => {
            const d = driversMap.current.get(id);
            if (!d) return;
            const vis = d.is_online ? filters.showOnline : filters.showOffline;
            const wasVis = driverVisibleRef.current.get(id) ?? false;
            if (vis && !wasVis) _marker.addTo(mapRef.current!);
            else if (!vis && wasVis) _marker.remove();
            driverVisibleRef.current.set(id, vis);
        });
        rideMarkersRef.current.forEach((_entry, id) => {
            setRideVisible(id, filters.showRides);
        });
    }, [filters, driversMap, isLoaded, setRideVisible]);

    // Follow selected driver
    useEffect(() => {
        if (!isLoaded || !followMode || !selected || selected.type !== "driver") return;
        const d = driversMap.current.get(selected.id);
        if (d?.lat && d.lng) panTo(d.lat, d.lng);
    });

    if (loadError) {
        return (
            <div className="flex h-full items-center justify-center bg-muted">
                <p className="text-sm text-destructive">
                    Failed to load map style. Check network / tile provider.
                </p>
            </div>
        );
    }

    return (
        <div ref={containerRef} style={MAP_CONTAINER_STYLE}>
            {!isLoaded && (
                <div className="absolute inset-0 flex items-center justify-center bg-muted">
                    <p className="text-sm text-muted-foreground">Loading map…</p>
                </div>
            )}
        </div>
    );
}
