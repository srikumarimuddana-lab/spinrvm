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
import {
    buildPathGradient,
    buildStraightRouteGradient,
    ROUTE_PIN_COLORS,
    ROUTE_STROKE_WIDTH,
} from "@spinr/shared/constants/routeMapStyle";

/**
 * Turn one or more real route segments (each a list of [lng, lat] positions, as
 * MapLibre GeoJSON stores them) into a FeatureCollection of orange→red gradient
 * chunks. Geometry is unchanged — every point is preserved; only the per-feature
 * `color` is derived from position along the path via the shared spec. Render
 * with paint `"line-color": ["get", "color"]`.
 */
function buildGradientFeatureCollection(
    segments: [number, number][][],
): GeoJSON.FeatureCollection {
    const features: GeoJSON.Feature[] = [];
    for (const segment of segments) {
        // Shared helper works in [lat, lng]; MapLibre coords are [lng, lat].
        const latLng = segment.map(([lng, lat]) => [lat, lng] as [number, number]);
        for (const chunk of buildPathGradient(latLng)) {
            features.push({
                type: "Feature",
                properties: { color: chunk.color },
                geometry: {
                    type: "LineString",
                    coordinates: chunk.coordinates.map(([lat, lng]) => [lng, lat]),
                },
            });
        }
    }
    return { type: "FeatureCollection", features };
}

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
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.pickup, size: 16 }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (red)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.dropoff, size: 16 }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Dropoff"))
                .addTo(map);

            // Road-following planned route (planned_route_polyline) — orange→red
            // gradient, same as every other route surface.
            if (hasPlannedTrail) {
                map.addSource(PLANNED_SOURCE_ID, {
                    type: "geojson",
                    data: buildGradientFeatureCollection([
                        plannedTrail!.map((p) => [p.lng, p.lat] as [number, number]),
                    ]),
                });
                map.addLayer({
                    id: PLANNED_LAYER_ID,
                    type: "line",
                    source: PLANNED_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": 0.7,
                    },
                });
            }

            // V2 captured trail. A MultiLineString preserves every recorded
            // capture gap; MapLibre will not draw a chord between segments.
            if (hasActualSegments) {
                map.addSource(ACTUAL_SOURCE_ID, {
                    type: "geojson",
                    // Each captured segment is coloured independently along the
                    // orange→red gradient; the MultiLineString's boundaries are
                    // preserved (no false chord between offline gaps).
                    data: buildGradientFeatureCollection(actualGeometry.coordinates),
                });
                map.addLayer({
                    id: ACTUAL_LAYER_ID,
                    type: "line",
                    source: ACTUAL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": 0.9,
                    },
                });
            }

            // Phase 2 (driver → pickup) — orange→red gradient, same as
            // every other route. Approximate trails draw at lower opacity.
            if (hasPickupTrail) {
                map.addSource(PICKUP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: buildGradientFeatureCollection([
                        pickupTrail!.map((p) => [p.lng, p.lat] as [number, number]),
                    ]),
                });
                map.addLayer({
                    id: PICKUP_TRAIL_LAYER_ID,
                    type: "line",
                    source: PICKUP_TRAIL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": pickupApprox ? 0.5 : 0.85,
                    },
                });
            }

            // Phase 3 (pickup → dropoff) — the real trip path, coloured as the
            // shared orange→red gradient.
            if (hasTripTrail) {
                map.addSource(TRIP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: buildGradientFeatureCollection([
                        tripTrail!.map((p) => [p.lng, p.lat] as [number, number]),
                    ]),
                });
                map.addLayer({
                    id: TRIP_TRAIL_LAYER_ID,
                    type: "line",
                    source: TRIP_TRAIL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
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
                    data: buildGradientFeatureCollection([
                        locationTrail.map((p) => [p.lng, p.lat] as [number, number]),
                    ]),
                });
                map.addLayer({
                    id: ACTUAL_LAYER_ID,
                    type: "line",
                    source: ACTUAL_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": 0.8,
                    },
                });
            }

            // Fallback: straight pickup→dropoff gradient when no other
            // route data exists. Uses buildStraightRouteGradient so it
            // matches the orange→red language everywhere else.
            if (!hasPlannedTrail && !hasRouteGeometry && !(locationTrail && locationTrail.length > 1)) {
                const straightGradient = buildStraightRouteGradient(
                    [pickupLat, pickupLng],
                    [dropoffLat, dropoffLng],
                );
                const features: GeoJSON.Feature[] = straightGradient.map((seg) => ({
                    type: "Feature" as const,
                    properties: { color: seg.color },
                    geometry: {
                        type: "LineString" as const,
                        coordinates: seg.coordinates.map(([lat, lng]) => [lng, lat]),
                    },
                }));
                map.addSource(PLANNED_SOURCE_ID, {
                    type: "geojson",
                    data: { type: "FeatureCollection", features },
                });
                map.addLayer({
                    id: PLANNED_LAYER_ID,
                    type: "line",
                    source: PLANNED_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": 0.7,
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
