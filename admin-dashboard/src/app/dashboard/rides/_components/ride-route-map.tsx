"use client";

import { useEffect, useMemo, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_URL,
    fitBoundsToPoints,
    makeCircleMarkerEl,
} from "@/lib/map/maplibre-base";
import { toGeoJsonMultiLineString } from "@spinr/shared/utils/routeSegments";

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    /** Legacy combined GPS trail — kept for backward compat with the
     *  phase-replay map below the drawer. Ignored when pickupTrail /
     *  tripTrail are provided. */
    locationTrail?: { lat: number; lng: number; timestamp?: string }[];
    /** Phase 2 (driver → pickup) GPS breadcrumbs. Rendered as a
     *  distinct amber road-following line. */
    pickupTrail?: { lat: number; lng: number; timestamp?: string }[];
    /** When true the pickupTrail is an *approximation* (no GPS was
     *  captured, e.g. a driver-start → pickup reference) — drawn dashed
     *  and lighter so it can't be mistaken for an actual GPS trace. */
    pickupApprox?: boolean;
    /** Phase 3 (pickup → dropoff) GPS breadcrumbs. Rendered as a
     *  distinct blue road-following line. */
    tripTrail?: { lat: number; lng: number; timestamp?: string }[];
    /** Road-following planned route (rides.planned_route_polyline, from the
     *  Directions API at booking). */
    plannedTrail?: { lat: number; lng: number }[];
    /** Version 2 captured route geometry. Each segment remains independent so
     *  an offline gap is not rendered as a false straight-line connection. */
    actualSegments?: unknown;
}

const PLANNED_SOURCE_ID = "ride-planned-src";
const PLANNED_LAYER_ID = "ride-planned-lyr";
const ACTUAL_SOURCE_ID = "ride-actual-src";
const ACTUAL_LAYER_ID = "ride-actual-lyr";
const PICKUP_TRAIL_SOURCE_ID = "ride-pickup-trail-src";
const PICKUP_TRAIL_LAYER_ID = "ride-pickup-trail-lyr";
const TRIP_TRAIL_SOURCE_ID = "ride-trip-trail-src";
const TRIP_TRAIL_LAYER_ID = "ride-trip-trail-lyr";

export default function RideRouteMap({
    pickupLat,
    pickupLng,
    dropoffLat,
    dropoffLng,
    locationTrail,
    pickupTrail,
    pickupApprox,
    tripTrail,
    plannedTrail,
    actualSegments,
}: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    // Memoize so the map-init effect (whose cleanup calls map.remove()) does not
    // tear down and rebuild the MapLibre map on every parent re-render — an
    // unmemoized new object here exhausts WebGL contexts and blanks the map.
    const actualGeometry = useMemo(() => toGeoJsonMultiLineString(actualSegments), [actualSegments]);

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const hasPickupTrail = !!pickupTrail && pickupTrail.length > 1;
        const hasTripTrail = !!tripTrail && tripTrail.length > 1;
        const hasPlannedTrail = !!plannedTrail && plannedTrail.length > 1;
        const hasActualSegments = actualGeometry.coordinates.length > 0;
        const hasRouteGeometry = hasActualSegments || hasPickupTrail || hasTripTrail || hasPlannedTrail;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [(pickupLng + dropoffLng) / 2, (pickupLat + dropoffLat) / 2],
            zoom: 13,
            // Static-summary view — no zoom buttons, just a compact
            // attribution badge so the map stays distraction-free.
            attributionControl: { compact: true },
        });
        mapRef.current = map;

        map.on("load", () => {
            // Pickup marker (green)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#10b981", size: 16 }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (red)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#ef4444", size: 16 }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Dropoff"))
                .addTo(map);

            // Road-following planned route (planned_route_polyline). Drawn
            // as a gray dashed line — the "planned" reference, but real road
            // geometry from the Directions API rather than a straight line.
            if (hasPlannedTrail) {
                map.addSource(PLANNED_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "Feature",
                        properties: {},
                        geometry: {
                            type: "LineString",
                            coordinates: plannedTrail!.map((p) => [p.lng, p.lat]),
                        },
                    },
                });
                map.addLayer({
                    id: PLANNED_LAYER_ID,
                    type: "line",
                    source: PLANNED_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": "#6b7280",
                        "line-width": 3,
                        "line-opacity": 0.7,
                        "line-dasharray": ["literal", [2, 1.5]],
                    },
                });
            }

            // V2 captured trail. A MultiLineString preserves every recorded
            // capture gap; MapLibre will not draw a chord between segments.
            if (hasActualSegments) {
                map.addSource(ACTUAL_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "Feature",
                        properties: {},
                        geometry: actualGeometry,
                    },
                });
                map.addLayer({
                    id: ACTUAL_LAYER_ID,
                    type: "line",
                    source: ACTUAL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: { "line-color": "#2563eb", "line-width": 3, "line-opacity": 0.9 },
                });
            }

            // Phase 2 (driver → pickup) — amber road-following line.
            if (hasPickupTrail) {
                map.addSource(PICKUP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "Feature",
                        properties: {},
                        geometry: {
                            type: "LineString",
                            coordinates: pickupTrail!.map((p) => [p.lng, p.lat]),
                        },
                    },
                });
                map.addLayer({
                    id: PICKUP_TRAIL_LAYER_ID,
                    type: "line",
                    source: PICKUP_TRAIL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": "#f59e0b",
                        "line-width": 3,
                        "line-opacity": pickupApprox ? 0.5 : 0.85,
                        // Approximate (no GPS) → dashed so it reads as a
                        // reference, not a recorded trace.
                        ...(pickupApprox ? { "line-dasharray": ["literal", [2, 2]] } : {}),
                    },
                });
            }

            // Phase 3 (pickup → dropoff) — blue road-following line.
            if (hasTripTrail) {
                map.addSource(TRIP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "Feature",
                        properties: {},
                        geometry: {
                            type: "LineString",
                            coordinates: tripTrail!.map((p) => [p.lng, p.lat]),
                        },
                    },
                });
                map.addLayer({
                    id: TRIP_TRAIL_LAYER_ID,
                    type: "line",
                    source: TRIP_TRAIL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": "#3b82f6",
                        "line-width": 3,
                        "line-opacity": 0.85,
                    },
                });
            }

            // Legacy combined trail — only render when no v2/phase
            // trail is available (pre-migration-39 rides still go through
            // this path via route_polyline).
            if (!hasRouteGeometry && locationTrail && locationTrail.length > 1) {
                map.addSource(ACTUAL_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "Feature",
                        properties: {},
                        geometry: {
                            type: "LineString",
                            coordinates: locationTrail.map((p) => [p.lng, p.lat]),
                        },
                    },
                });
                map.addLayer({
                    id: ACTUAL_LAYER_ID,
                    type: "line",
                    source: ACTUAL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": "#3b82f6",
                        "line-width": 3,
                        "line-opacity": 0.8,
                    },
                });
            }

            // Fit bounds over every point we actually drew.
            const allPoints: { lat: number; lng: number }[] = [
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                ...(pickupTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
                ...(tripTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
                ...(plannedTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
                ...actualGeometry.coordinates.reduce<{ lat: number; lng: number }[]>(
                    (points, segment) => points.concat(segment.map(([lng, lat]) => ({ lat, lng }))),
                    [],
                ),
                ...(!hasRouteGeometry ? (locationTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })) : []),
            ];
            fitBoundsToPoints(map, allPoints, 40);
        });

        return () => {
            map.remove();
            mapRef.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail, pickupTrail, pickupApprox, tripTrail, plannedTrail, actualGeometry]);

    return <div ref={containerRef} className="w-full h-[280px] rounded-xl overflow-hidden" />;
}
