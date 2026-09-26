/// <reference types="geojson" />
"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    primaryMapStyle,
    addStandardControls,
    fitBoundsToPoints,
    makeCircleMarkerEl,
    makeRoutePinEl,
} from "@/lib/map/maplibre-base";
import { hasRenderingWebGL } from "@/lib/map/webgl-support";
import {
    MARKER_ANIMATION_MS,
    interpolateMarker,
    prefersReducedMotion,
    type MarkerPose,
} from "@/lib/map/marker-interpolation";
import {
    buildPathGradient,
    buildStraightRouteGradient,
    ROUTE_STROKE_WIDTH,
} from "@spinr/shared/constants/routeMapStyle";

/**
 * Colour a real trail (list of {lat, lng}) as an orange→red gradient
 * FeatureCollection — geometry unchanged, only per-feature `color` derived from
 * position along the path via the shared spec. Render with `["get", "color"]`.
 */
function trailGradientFeatureCollection(
    trail: { lat: number; lng: number }[],
): GeoJSON.FeatureCollection {
    const features: GeoJSON.Feature[] = [];
    for (const chunk of buildPathGradient(trail.map((p) => [p.lat, p.lng] as [number, number]))) {
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

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    driverLat?: number;
    driverLng?: number;
    trail?: { lat: number; lng: number }[];
}

const TRAIL_SOURCE_ID = "live-trail-src";
const TRAIL_LAYER_ID = "live-trail-lyr";
const PLANNED_SOURCE_ID = "live-planned-src";
const PLANNED_LAYER_ID = "live-planned-lyr";

export default function LiveRideMap({ pickupLat, pickupLng, dropoffLat, dropoffLng, driverLat, driverLng, trail }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const driverMarkerRef = useRef<maplibregl.Marker | null>(null);
    const isLoadedRef = useRef(false);
    // Lazy initializer, computed once — same idiom ride-route-map.tsx and
    // monitoring-map.tsx already use for this probe. Without it, a browser
    // whose WebGL context is a non-drawing stub (common with privacy/ad-
    // blocking extensions) still gets a MapLibre map built here: MapLibre's
    // own "load" event only reflects style/tile JSON reaching the page, not
    // the GPU actually painting, so the canvas would sit blank with nothing
    // on screen explaining why. See src/lib/map/webgl-support.ts.
    const [webglOk] = useState<boolean>(() => hasRenderingWebGL());

    // ── Driver marker glide (UX program W4.1) ────────────────────────
    // Same pattern as monitoring-map.tsx and the public /track page: the pose
    // drawn now (`shown`), the glide in progress (`from` → `to`, started at
    // `startedAt`, null when idle) and when the last move arrived (for the
    // util's stale-gap snap). The marker's first placement is exact; later
    // moves glide over MARKER_ANIMATION_MS, or snap on a jump over 500 m, a
    // stale feed or Reduce Motion.
    const driverMotionRef = useRef<{
        shown: MarkerPose; from: MarkerPose; to: MarkerPose; startedAt: number | null; updatedAt: number;
    } | null>(null);
    const driverFrameRef = useRef<number | null>(null);

    const stopDriverMotion = useCallback(() => {
        if (driverFrameRef.current != null) cancelAnimationFrame(driverFrameRef.current);
        driverFrameRef.current = null;
        driverMotionRef.current = null;
    }, []);

    /** Move the driver marker to `to`: glide from wherever it is drawn now,
     *  or set it there directly when the util says snap (nothing drawn yet,
     *  nothing moved, a jump over 500 m, a stale feed, Reduce Motion). */
    const moveDriverMarker = useCallback((marker: maplibregl.Marker, to: MarkerPose) => {
        const now = performance.now();
        const prev = driverMotionRef.current;
        // A glide already under way retargets from the pose drawn right now.
        const from = prev?.shown ?? to;
        const { done } = interpolateMarker(from, to, 0, MARKER_ANIMATION_MS, {
            reduceMotion: prefersReducedMotion(),
            gapMs: prev ? now - prev.updatedAt : undefined,
        });
        if (done) {
            if (driverFrameRef.current != null) cancelAnimationFrame(driverFrameRef.current);
            driverFrameRef.current = null;
            driverMotionRef.current = { shown: to, from: to, to, startedAt: null, updatedAt: now };
            marker.setLngLat([to.lng, to.lat]);
            return;
        }
        driverMotionRef.current = { shown: from, from, to, startedAt: now, updatedAt: now };
        if (driverFrameRef.current != null) return; // the running loop picks up the new target
        const step = (t: number) => {
            driverFrameRef.current = null;
            const m = driverMotionRef.current;
            const current = driverMarkerRef.current;
            if (!m || m.startedAt == null || !current) return;
            const { pose, done: arrived } = interpolateMarker(m.from, m.to, t - m.startedAt, MARKER_ANIMATION_MS);
            m.shown = pose;
            current.setLngLat([pose.lng, pose.lat]);
            if (arrived) m.startedAt = null;
            else driverFrameRef.current = requestAnimationFrame(step);
        };
        driverFrameRef.current = requestAnimationFrame(step);
    }, []);

    // Initialize map once
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;
        if (!webglOk) return;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: primaryMapStyle(),
            center: [(pickupLng + dropoffLng) / 2, (pickupLat + dropoffLat) / 2],
            zoom: 13,
        });
        addStandardControls(map);
        mapRef.current = map;

        map.on("load", () => {
            isLoadedRef.current = true;

            // Pickup marker (green)
            new maplibregl.Marker({
                element: makeRoutePinEl({ kind: "pickup", size: 26, title: "Pickup" }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (red)
            new maplibregl.Marker({
                element: makeRoutePinEl({ kind: "dropoff", size: 26, title: "Dropoff" }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Dropoff"))
                .addTo(map);

            // Planned route — straight-line orange→red gradient.
            const straightGradient = buildStraightRouteGradient(
                [pickupLat, pickupLng],
                [dropoffLat, dropoffLng],
            );
            const plannedFeatures: GeoJSON.Feature[] = straightGradient.map((seg) => ({
                type: "Feature" as const,
                properties: { color: seg.color },
                geometry: {
                    type: "LineString" as const,
                    coordinates: seg.coordinates.map(([lat, lng]) => [lng, lat]),
                },
            }));
            map.addSource(PLANNED_SOURCE_ID, {
                type: "geojson",
                data: { type: "FeatureCollection", features: plannedFeatures },
            });
            map.addLayer({
                id: PLANNED_LAYER_ID,
                type: "line",
                source: PLANNED_SOURCE_ID,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": ["get", "color"],
                    "line-width": ROUTE_STROKE_WIDTH,
                    "line-opacity": 0.6,
                },
            });

            // Trail source + layer (actual driver path) — orange→red gradient.
            map.addSource(TRAIL_SOURCE_ID, {
                type: "geojson",
                data: { type: "FeatureCollection", features: [] },
            });
            map.addLayer({
                id: TRAIL_LAYER_ID,
                type: "line",
                source: TRAIL_SOURCE_ID,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": ["get", "color"],
                    "line-width": ROUTE_STROKE_WIDTH,
                    "line-opacity": 0.9,
                },
            });

            // Driver marker (amber) — placeholder, updated by the other effect
            driverMarkerRef.current = new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#f59e0b", size: 18 }),
            })
                .setLngLat([
                    driverLng ?? (pickupLng + dropoffLng) / 2,
                    driverLat ?? (pickupLat + dropoffLat) / 2,
                ])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Driver"))
                .addTo(map);
            // Only a real position is a glide start point; the midpoint
            // placeholder isn't, so the first real fix is set directly.
            if (driverLat != null && driverLng != null) {
                const placed = { lat: driverLat, lng: driverLng };
                driverMotionRef.current = {
                    shown: placed, from: placed, to: placed, startedAt: null, updatedAt: performance.now(),
                };
            }

            fitBoundsToPoints(
                map,
                [
                    { lat: pickupLat, lng: pickupLng },
                    { lat: dropoffLat, lng: dropoffLng },
                ],
                60,
            );
        });

        return () => {
            stopDriverMotion();
            driverMarkerRef.current?.remove();
            driverMarkerRef.current = null;
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, webglOk, stopDriverMotion]);

    // Update driver position and trail
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;

        if (driverLat != null && driverLng != null && driverMarkerRef.current) {
            moveDriverMarker(driverMarkerRef.current, { lat: driverLat, lng: driverLng });
        }

        if (trail && trail.length > 0) {
            const src = map.getSource(TRAIL_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
            src?.setData(trailGradientFeatureCollection(trail));
        }
    }, [driverLat, driverLng, trail, moveDriverMarker]);

    if (!webglOk) {
        return (
            <div
                role="status"
                className="flex h-full min-h-[400px] w-full items-center justify-center bg-muted px-6 text-center"
            >
                <p className="text-sm text-muted-foreground">
                    Live map can&apos;t render in this browser — often an ad or
                    privacy blocker. Try disabling it for this site, or use a
                    different browser. Ride status, route, and driver/rider
                    details in the panel are still live.
                </p>
            </div>
        );
    }

    return <div ref={containerRef} className="w-full h-full min-h-[400px]" />;
}
