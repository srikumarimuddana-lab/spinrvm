// src/app/dashboard/monitoring/monitoring-map.tsx
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    DEFAULT_CENTER,
    MAP_STYLE_URL,
    addStandardControls,
    fitBoundsToGeoJSON,
    makeCircleMarkerEl,
    makeRoutePinEl,
} from "@/lib/map/maplibre-base";
import {
    buildPathGradient,
    ROUTE_STROKE_WIDTH,
} from "@spinr/shared/constants/routeMapStyle";
import { AreaDemandSupply, MonitoringDriver, MonitoringFilters, MonitoringRide, SelectedItem } from "./types";
import {
    DEMAND_BANDS,
    NO_DATA_COLOR,
    bandRangeLabel,
    demandFillColor,
    demandFillOpacity,
} from "@/lib/demand-bands";

const DEFAULT_ZOOM = 12;

/**
 * Colour a real ride line (OSRM road route or the interim straight fallback,
 * both as [lng, lat] positions) as the shared orange→red gradient. Geometry is
 * unchanged — every coordinate is kept; only the per-feature `color` is derived
 * from position along the path. Render with paint `["get", "color"]`.
 */
function rideLineGradientFeatureCollection(
    coords: [number, number][],
): GeoJSON.FeatureCollection {
    const features: GeoJSON.Feature[] = [];
    for (const chunk of buildPathGradient(coords.map(([lng, lat]) => [lat, lng] as [number, number]))) {
        features.push({
            type: "Feature",
            properties: { color: chunk.color },
            geometry: {
                type: "LineString",
                coordinates: chunk.coordinates.map(([lat, lng]) => [lng, lat]),
            },
        });
    }
    return { type: "FeatureCollection", features };
}

// Cache OSRM route lookups per ride_id so panning / re-renders don't re-fetch.
// In-memory only — clears on hard reload, which is the desired behaviour
// (refetches a fresh route if pickup/dropoff coords ever change).
const routeCache = new Map<string, [number, number][]>();
const routeInFlight = new Set<string>();

/**
 * Fetch a road-following polyline between pickup and dropoff from OSRM
 * (free OpenStreetMap routing server). Returns the coordinate list as
 * [lng, lat] pairs ready for a GeoJSON LineString, or null on failure.
 *
 * Used by the Live Monitoring map so the ride route renders along the
 * actual streets instead of a straight line between pickup and dropoff.
 * The OSRM demo server is rate-limited but our admin volume is tiny —
 * one request per ride per page load, deduplicated by the in-memory cache.
 */
async function fetchOSRMRoute(
    rideId: string,
    pickup: [number, number],
    dropoff: [number, number],
): Promise<[number, number][] | null> {
    if (routeCache.has(rideId)) return routeCache.get(rideId)!;
    if (routeInFlight.has(rideId)) return null;
    routeInFlight.add(rideId);
    try {
        const url =
            `https://router.project-osrm.org/route/v1/driving/` +
            `${pickup[0]},${pickup[1]};${dropoff[0]},${dropoff[1]}` +
            `?overview=full&geometries=geojson`;
        const res = await fetch(url);
        if (!res.ok) return null;
        const data = await res.json();
        const coords: [number, number][] | undefined =
            data?.routes?.[0]?.geometry?.coordinates;
        if (!coords || coords.length < 2) return null;
        routeCache.set(rideId, coords);
        return coords;
    } catch {
        return null;
    } finally {
        routeInFlight.delete(rideId);
    }
}

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

// Service-area rendering: one GeoJSON source feeding a fill + line layer
const AREAS_SOURCE_ID = "monitoring-areas-src";
const AREAS_FILL_LAYER_ID = "monitoring-areas-fill";
const AREAS_LINE_LAYER_ID = "monitoring-areas-line";

export interface MonitoringServiceArea {
    id: string;
    name: string;
    geojson?: GeoJSON.Polygon | GeoJSON.MultiPolygon | null;
    /** Fallback point to centre the map on when no polygon exists (e.g. city preset). */
    fallbackCenter?: { lat: number; lng: number } | null;
}

interface MonitoringMapProps {
    driversMap: React.MutableRefObject<Map<string, MonitoringDriver>>;
    ridesMap: React.MutableRefObject<Map<string, MonitoringRide>>;
    filters: MonitoringFilters;
    searchQuery: string;
    selected: SelectedItem;
    followMode: boolean;
    serviceAreas?: MonitoringServiceArea[];
    demandData?: AreaDemandSupply[];
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
    fitArea: (areaId: string) => void;
}

function rideLineSourceId(rideId: string): string {
    return `ride-line-src-${rideId}`;
}
function rideLineLayerId(rideId: string): string {
    return `ride-line-lyr-${rideId}`;
}

const MAP_CONTAINER_STYLE: React.CSSProperties = { width: "100%", height: "100%" };

/** Pre-overlay appearance — unchanged from before the demand feature existed,
 *  so toggling the overlay off is pixel-identical to the original map. */
const DEFAULT_AREA_FILL = "#8b5cf6";
const DEFAULT_AREA_OPACITY = 0.08;

/** Fill colour for a service-area polygon when the demand overlay is on.
 *
 * Thresholds and colours come from the shared band module so this map and the
 * heatmap page's cards can never disagree about what a ratio means. An area
 * missing from the response (airport sub-zones, which get_surge_status skips,
 * or a brand-new area) is NOT the same as an oversupplied one — it gets the
 * neutral no-data treatment rather than being painted "Oversupply" purple,
 * which previously made a surge-engine outage look like a healthy market.
 *
 * Exported for tests: this is AD-01's own wiring (the per-area lookup, the
 * overlay gate and the missing-area fallback), and it is the part the shared
 * band module cannot cover. Rendering MonitoringMap needs maplibre-gl, which
 * is why the smoke suite stubs this file — leaving this logic at 0%. */
export function areaFillColor(areaId: string, demandData: AreaDemandSupply[] | undefined, showDemand: boolean): string {
    if (!showDemand || !demandData) return DEFAULT_AREA_FILL;
    const d = demandData.find(a => a.area_id === areaId);
    if (!d) return NO_DATA_COLOR;
    return demandFillColor(d.demand_count, d.supply_count, d.ratio);
}

export function areaFillOpacity(areaId: string, demandData: AreaDemandSupply[] | undefined, showDemand: boolean): number {
    if (!showDemand || !demandData) return DEFAULT_AREA_OPACITY;
    const d = demandData.find(a => a.area_id === areaId);
    if (!d) return DEFAULT_AREA_OPACITY;
    return demandFillOpacity(d.demand_count, d.supply_count, d.ratio);
}

export function MonitoringMap({
    driversMap,
    ridesMap,
    filters,
    searchQuery,
    selected,
    followMode,
    serviceAreas,
    demandData,
    onSelectDriver,
    onSelectRide,
    onReady,
}: MonitoringMapProps) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const [isLoaded, setIsLoaded] = useState(false);
    const [loadError, setLoadError] = useState<string | null>(null);

    const driverMarkersRef = useRef<Map<string, maplibregl.Marker>>(new Map());
    const driverVisibleRef = useRef<Map<string, boolean>>(new Map());

    const rideMarkersRef = useRef<
        Map<string, { pickup: maplibregl.Marker; dropoff: maplibregl.Marker; sourceId: string; layerId: string }>
    >(new Map());
    const rideVisibleRef = useRef<Map<string, boolean>>(new Map());

    const onSelectDriverRef = useRef(onSelectDriver);
    onSelectDriverRef.current = onSelectDriver;
    const onSelectRideRef = useRef(onSelectRide);
    onSelectRideRef.current = onSelectRide;

    const serviceAreasRef = useRef<MonitoringServiceArea[]>(serviceAreas ?? []);
    serviceAreasRef.current = serviceAreas ?? [];

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
            const el = makeCircleMarkerEl({
                color: driverColor(driver),
                label: driver.name.slice(0, 1).toUpperCase(),
                title: driver.name,
                size: 22,
            });
            el.addEventListener("click", () => onSelectDriverRef.current(driver.id));
            marker = new maplibregl.Marker({ element: el }).setLngLat(lngLat);
            if (visible) marker.addTo(mapRef.current);
            driverMarkersRef.current.set(driver.id, marker);
            driverVisibleRef.current.set(driver.id, visible);
        } else {
            marker.setLngLat(lngLat);
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

        if (
            ride.pickup_lat == null || ride.pickup_lng == null ||
            ride.dropoff_lat == null || ride.dropoff_lng == null
        ) return;

        let entry = rideMarkersRef.current.get(ride.id);
        const pickupLngLat: [number, number] = [ride.pickup_lng, ride.pickup_lat];
        const dropoffLngLat: [number, number] = [ride.dropoff_lng, ride.dropoff_lat];

        if (!entry) {
            const pickupEl = makeRoutePinEl({
                kind: "pickup",
                title: `Pickup: ${ride.pickup_address ?? ""}`,
                size: 22,
            });
            pickupEl.addEventListener("click", () => onSelectRideRef.current(ride.id));
            const pickup = new maplibregl.Marker({ element: pickupEl }).setLngLat(pickupLngLat);

            const dropoffEl = makeRoutePinEl({
                kind: "dropoff",
                title: `Dropoff: ${ride.dropoff_address ?? ""}`,
                size: 22,
            });
            dropoffEl.addEventListener("click", () => onSelectRideRef.current(ride.id));
            const dropoff = new maplibregl.Marker({ element: dropoffEl }).setLngLat(dropoffLngLat);

            const sourceId = rideLineSourceId(ride.id);
            const layerId = rideLineLayerId(ride.id);

            mapRef.current.addSource(sourceId, {
                type: "geojson",
                data: rideLineGradientFeatureCollection([pickupLngLat, dropoffLngLat]),
            });
            mapRef.current.addLayer({
                id: layerId,
                type: "line",
                source: sourceId,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": ["get", "color"],
                    "line-width": ROUTE_STROKE_WIDTH,
                    "line-opacity": 0.75,
                },
            });

            entry = { pickup, dropoff, sourceId, layerId };
            rideMarkersRef.current.set(ride.id, entry);
            rideVisibleRef.current.set(ride.id, false);

            if (filters.showRides) {
                setRideVisible(ride.id, true);
            } else {
                mapRef.current.setLayoutProperty(layerId, "visibility", "none");
            }

            // Kick off async OSRM lookup; when it returns, replace the straight
            // pickup→dropoff line with a road-following polyline. The line
            // already shows as straight in the meantime so the rider is never
            // staring at a blank map waiting for routing.
            void fetchOSRMRoute(ride.id, pickupLngLat, dropoffLngLat).then((coords) => {
                if (!coords || !mapRef.current) return;
                const src = mapRef.current.getSource(sourceId);
                if (src && "setData" in src) {
                    (src as maplibregl.GeoJSONSource).setData(
                        rideLineGradientFeatureCollection(coords),
                    );
                }
            });
        } else {
            entry.pickup.setLngLat(pickupLngLat);
            entry.dropoff.setLngLat(dropoffLngLat);
            const src = mapRef.current.getSource(entry.sourceId);
            if (src && "setData" in src) {
                // Prefer the cached OSRM route when we have one — keeps the
                // road-following line stable across panel updates. Falls
                // back to the straight line until OSRM responds.
                const cached = routeCache.get(ride.id);
                const coords: [number, number][] = cached ?? [pickupLngLat, dropoffLngLat];
                (src as maplibregl.GeoJSONSource).setData(
                    rideLineGradientFeatureCollection(coords),
                );
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

    const fitArea = useCallback((areaId: string) => {
        const map = mapRef.current;
        if (!map) return;
        const area = serviceAreasRef.current.find((a) => a.id === areaId);
        if (!area) return;
        if (area.geojson) {
            fitBoundsToGeoJSON(map, area.geojson, 60);
            return;
        }
        // No polygon drawn yet — fall back to the city preset centre if
        // we were given one, so the operator still sees the right city.
        if (area.fallbackCenter) {
            map.flyTo({
                center: [area.fallbackCenter.lng, area.fallbackCenter.lat],
                zoom: 11,
                duration: 600,
            });
        }
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

            addStandardControls(map);
            map.addControl(new maplibregl.FullscreenControl(), "top-right");

            map.on("load", () => {
                if (cancelled) return;

                // Service-area polygons layer (rendered under markers)
                map.addSource(AREAS_SOURCE_ID, {
                    type: "geojson",
                    data: { type: "FeatureCollection", features: [] },
                });
                map.addLayer({
                    id: AREAS_FILL_LAYER_ID,
                    type: "fill",
                    source: AREAS_SOURCE_ID,
                    paint: { "fill-color": "#8b5cf6", "fill-opacity": 0.08 },
                });
                map.addLayer({
                    id: AREAS_LINE_LAYER_ID,
                    type: "line",
                    source: AREAS_SOURCE_ID,
                    paint: {
                        "line-color": "#8b5cf6",
                        "line-width": 2,
                        "line-dasharray": ["literal", [3, 2]],
                    },
                });

                setIsLoaded(true);
                driversMap.current.forEach(updateDriverMarker);
                ridesMap.current.forEach(updateRideMarkers);
                onReady({
                    updateDriverMarker,
                    removeDriverMarker,
                    updateRideMarkers,
                    removeRideMarkers,
                    panTo,
                    fitArea,
                });
            });
            map.on("error", (e) => {
                if (cancelled) return;
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
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Keep the areas source in sync with the prop + demand overlay
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoaded) return;
        const features: GeoJSON.Feature[] = [];
        (serviceAreas ?? []).forEach((area) => {
            if (!area.geojson) return;
            features.push({
                type: "Feature",
                properties: {
                    id: area.id,
                    name: area.name ?? "Service Area",
                    fillColor: areaFillColor(area.id, demandData, filters.showDemand),
                    fillOpacity: areaFillOpacity(area.id, demandData, filters.showDemand),
                },
                geometry: area.geojson,
            });
        });
        const src = map.getSource(AREAS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
        src?.setData({ type: "FeatureCollection", features });

        const fillLayer = map.getLayer(AREAS_FILL_LAYER_ID);
        if (fillLayer) {
            map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-color", ["get", "fillColor"]);
            map.setPaintProperty(AREAS_FILL_LAYER_ID, "fill-opacity", ["get", "fillOpacity"]);
        }
    }, [serviceAreas, isLoaded, demandData, filters.showDemand]);

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
            {demandData && demandData.length > 0 && (
                // bg-background (not /90): a translucent panel over live map tiles
                // put the muted legend text right at the contrast floor with the
                // underlying imagery unknowable.
                <div className="absolute bottom-3 left-3 z-10 rounded-lg border border-border bg-background p-3 text-xs shadow-md">
                    <p className="mb-1.5 font-medium">Demand : supply ratio</p>
                    {DEMAND_BANDS.map((band) => (
                        <div key={band.key} className="flex items-center gap-2 py-0.5">
                            <span
                                aria-hidden="true"
                                className="inline-block h-3 w-3 rounded-sm"
                                style={{ backgroundColor: band.color, opacity: 0.7 }}
                            />
                            <span className="text-foreground">
                                {band.label}{" "}
                                <span className="text-muted-foreground">
                                    ({bandRangeLabel(band)}) → {band.multiplier.toFixed(2)}× fare
                                </span>
                            </span>
                        </div>
                    ))}
                    <div className="mt-1 flex items-center gap-2 border-t border-border pt-1.5">
                        <span
                            aria-hidden="true"
                            className="inline-block h-3 w-3 rounded-sm"
                            style={{ backgroundColor: NO_DATA_COLOR, opacity: 0.7 }}
                        />
                        <span className="text-muted-foreground">No data reported</span>
                    </div>
                </div>
            )}
        </div>
    );
}
