/// <reference types="geojson" />
"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import * as maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    attachBasemapFallback,
    basemapChain,
    fitBoundsToPoints,
    makeRoutePinEl,
} from "@/lib/map/maplibre-base";
import { hasRenderingWebGL } from "@/lib/map/webgl-support";
import StaticRouteMap from "./static-route-map";
import { toGeoJsonMultiLineString } from "@spinr/shared/utils/routeSegments";
import {
    buildPathGradient,
    buildStraightRouteGradient,
    ROUTE_GRADIENT_START,
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
    /** Suppress the pickup→dropoff straight-line fallback, leaving just the two
     *  markers. Set when the absence of geometry is itself the finding — e.g. a
     *  legacy-imported ride that never had GPS — so the map cannot imply a path
     *  that was never recorded. Defaults to false: every existing caller keeps
     *  the fallback. */
    suppressStraightFallback?: boolean;
}

const PLANNED_SOURCE_ID = "ride-planned-src";
const PLANNED_LAYER_ID = "ride-planned-lyr";
const ACTUAL_SOURCE_ID = "ride-actual-src";
const ACTUAL_LAYER_ID = "ride-actual-lyr";
const PICKUP_TRAIL_SOURCE_ID = "ride-pickup-trail-src";
const PICKUP_TRAIL_LAYER_ID = "ride-pickup-trail-lyr";
const TRIP_TRAIL_SOURCE_ID = "ride-trip-trail-src";
const TRIP_TRAIL_LAYER_ID = "ride-trip-trail-lyr";

const ROUTE_LAYER_PAIRS: readonly (readonly [string, string])[] = [
    [PLANNED_LAYER_ID, PLANNED_SOURCE_ID],
    [ACTUAL_LAYER_ID, ACTUAL_SOURCE_ID],
    [PICKUP_TRAIL_LAYER_ID, PICKUP_TRAIL_SOURCE_ID],
    [TRIP_TRAIL_LAYER_ID, TRIP_TRAIL_SOURCE_ID],
];

/** Drop every route layer/source this component owns, so a redraw (new phase
 *  data, or a basemap provider swap) is idempotent rather than throwing
 *  "source already exists". */
function clearRouteLayers(map: maplibregl.Map): void {
    for (const [layerId, sourceId] of ROUTE_LAYER_PAIRS) {
        if (map.getLayer(layerId)) map.removeLayer(layerId);
        if (map.getSource(sourceId)) map.removeSource(sourceId);
    }
}

function finiteTrail(
    pts: { lat: number; lng: number }[] | undefined,
): { lat: number; lng: number }[] {
    return (pts ?? []).filter((p) =>
        Number.isFinite(p.lat) && Number.isFinite(p.lng)
        && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180,
    );
}

function routeLineData(lngLatPath: [number, number][]): GeoJSON.FeatureCollection {
    const gradient = buildGradientFeatureCollection([lngLatPath]);
    if (gradient.features.length > 0) return gradient;
    if (lngLatPath.length < 2) return { type: "FeatureCollection", features: [] };
    return {
        type: "FeatureCollection",
        features: [{
            type: "Feature",
            properties: { color: ROUTE_GRADIENT_START },
            geometry: { type: "LineString", coordinates: lngLatPath },
        }],
    };
}

function restackRouteLayers(map: maplibregl.Map): void {
    for (const [layerId] of ROUTE_LAYER_PAIRS) {
        if (map.getLayer(layerId)) map.moveLayer(layerId);
    }
}

function hasRouteLayer(map: maplibregl.Map): boolean {
    return ROUTE_LAYER_PAIRS.some(([layerId]) => !!map.getLayer(layerId));
}

type OverlayStroke = {
    coords: [number, number][];
    color: string;
    opacity: number;
};

const OVERLAY_CLASS = "ride-route-svg-overlay";
const overlayStrokesByMap = new WeakMap<maplibregl.Map, OverlayStroke[]>();

/** Pins are DOM markers, so they stay visible even when a GL line is buried
 *  under a late-loading basemap fill (or wiped by a styledata redraw). Draw
 *  the route in that same overlay, projected with map.project(). */
function syncRouteSvgOverlay(map: maplibregl.Map, strokes: OverlayStroke[]): void {
    overlayStrokesByMap.set(map, strokes);
    const root = map.getContainer();
    const canvasHost = map.getCanvasContainer();
    let svg = root.querySelector(`svg.${OVERLAY_CLASS}`) as SVGSVGElement | null;
    if (!svg) {
        svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", OVERLAY_CLASS);
        svg.setAttribute("aria-hidden", "true");
        // Sibling of the canvas (not inside it): map.project() is in container
        // pixels, and a GL transform on the canvas must not double-offset the
        // line. z-index 0 keeps MapLibre markers (later siblings) on top.
        svg.style.cssText =
            "position:absolute;inset:0;width:100%;height:100%;pointer-events:none;z-index:0;overflow:visible;";
        canvasHost.after(svg);
    }
    const w = root.clientWidth;
    const h = root.clientHeight;
    svg.setAttribute("viewBox", `0 0 ${w} ${h}`);
    svg.setAttribute("width", String(w));
    svg.setAttribute("height", String(h));
    svg.replaceChildren();
    for (const stroke of strokes) {
        if (stroke.coords.length < 2) continue;
        const points = stroke.coords
            .map(([lng, lat]) => {
                const p = map.project([lng, lat]);
                return `${p.x},${p.y}`;
            })
            .join(" ");
        const casing = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        casing.setAttribute("points", points);
        casing.setAttribute("fill", "none");
        casing.setAttribute("stroke", "#ffffff");
        casing.setAttribute("stroke-opacity", "0.9");
        casing.setAttribute("stroke-width", String(ROUTE_STROKE_WIDTH + 3));
        casing.setAttribute("stroke-linecap", "round");
        casing.setAttribute("stroke-linejoin", "round");
        svg.appendChild(casing);
        const line = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
        line.setAttribute("points", points);
        line.setAttribute("fill", "none");
        line.setAttribute("stroke", stroke.color);
        line.setAttribute("stroke-opacity", String(stroke.opacity));
        line.setAttribute("stroke-width", String(ROUTE_STROKE_WIDTH));
        line.setAttribute("stroke-linecap", "round");
        line.setAttribute("stroke-linejoin", "round");
        svg.appendChild(line);
    }
}

function strokesFromLngLatPath(
    lngLatPath: [number, number][],
    opacity: number,
): OverlayStroke[] {
    const data = routeLineData(lngLatPath);
    const out: OverlayStroke[] = [];
    for (const feature of data.features) {
        if (feature.geometry?.type !== "LineString") continue;
        const coords = feature.geometry.coordinates
            .filter((c): c is [number, number] =>
                Array.isArray(c) && c.length >= 2
                && Number.isFinite(c[0]) && Number.isFinite(c[1]),
            )
            .map(([lng, lat]) => [lng, lat] as [number, number]);
        if (coords.length < 2) continue;
        const color = typeof feature.properties?.color === "string"
            ? feature.properties.color
            : ROUTE_GRADIENT_START;
        out.push({ coords, color, opacity });
    }
    return out;
}

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
    suppressStraightFallback = false,
}: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    // "ok" shows nothing at all, so a basemap that loads first try — the normal
    // case — never flashes a banner. "retrying" only appears once a hop has
    // actually failed, which is the 8-24s window that would otherwise be silent.
    const [basemapStatus, setBasemapStatus] = useState<"ok" | "retrying" | "failed">("ok");
    // Probed once, lazily, on first render. Safe here because ride-detail-modal
    // loads this component with ssr:false, so there is always a document; doing
    // it in an effect instead would cost an extra render and briefly mount a
    // MapLibre map we may be about to discard.
    const [webglOk] = useState<boolean>(() => hasRenderingWebGL());
    // Memoize so the draw effect does not rebuild the geometry on every parent
    // re-render — an unmemoized new object here churns the route layers.
    const actualGeometry = useMemo(() => toGeoJsonMultiLineString(actualSegments), [actualSegments]);

    // Hand off to the no-WebGL renderer when MapLibre either cannot run at all
    // or has exhausted every basemap provider.
    const useStatic = !webglOk || basemapStatus === "failed";

    // The same trail-priority the MapLibre draw path applies, flattened into
    // plain polylines for the raster renderer. Only computed when it is needed.
    const staticPaths = useMemo(() => {
        if (!useStatic) return undefined;
        const out: { points: { lat: number; lng: number }[]; approx?: boolean }[] = [];
        const push = (pts: { lat: number; lng: number }[] | undefined, approx?: boolean) => {
            if (pts && pts.length > 1) out.push({ points: pts, approx });
        };
        push(plannedTrail);
        for (const segment of actualGeometry.coordinates) {
            push(segment.map(([lng, lat]) => ({ lat, lng })));
        }
        push(pickupTrail, pickupApprox);
        push(tripTrail);
        if (out.length === 0) push(locationTrail);
        return out;
    }, [useStatic, plannedTrail, actualGeometry, pickupTrail, pickupApprox, tripTrail, locationTrail]);

    // The latest "draw the route onto this map" routine, held in a ref so the
    // map-creation effect below can call it without taking the route data as a
    // dependency.
    //
    // That dependency is what used to destroy and rebuild the entire WebGL map
    // on every parent re-render: ride-detail-modal.tsx builds pickupTrail /
    // tripTrail / plannedTrail / locationTrail as fresh arrays inside its render
    // body, so their identity changes every render, and this effect's cleanup
    // calls map.remove(). Repeated teardown exhausts WebGL contexts and leaves
    // the canvas blank — the failure the memo note above was added for, which
    // had only ever been fixed for actualSegments. The map effect now depends on
    // primitives alone; data changes redraw layers instead of rebuilding the map.
    const drawRef = useRef<(map: maplibregl.Map, fit: boolean) => void>(() => {});

    // ── Draw the route data ───────────────────────────────────────────────────
    // Re-runs whenever the route data changes; redraws layers on the existing
    // map instead of recreating it.
    useEffect(() => {
        drawRef.current = (map: maplibregl.Map, fit: boolean) => {
            const pickupPts = finiteTrail(pickupTrail);
            const tripPts = finiteTrail(tripTrail);
            const plannedPts = finiteTrail(plannedTrail);
            const locationPts = finiteTrail(locationTrail);
            const hasPickupTrail = pickupPts.length > 1;
            const hasTripTrail = tripPts.length > 1;
            const hasPlannedTrail = plannedPts.length > 1;
            const hasActualSegments = actualGeometry.coordinates.length > 0;
            const hasRouteGeometry = hasActualSegments || hasPickupTrail || hasTripTrail || hasPlannedTrail;
            const overlayStrokes: OverlayStroke[] = [];

            try {
            clearRouteLayers(map);

            // Road-following planned route (planned_route_polyline) — orange→red
            // gradient, same as every other route surface.
            if (hasPlannedTrail) {
                const plannedLngLat = plannedPts.map((p) => [p.lng, p.lat] as [number, number]);
                overlayStrokes.push(...strokesFromLngLatPath(plannedLngLat, 0.95));
                map.addSource(PLANNED_SOURCE_ID, {
                    type: "geojson",
                    data: routeLineData(plannedLngLat),
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
                for (const segment of actualGeometry.coordinates) {
                    overlayStrokes.push(...strokesFromLngLatPath(
                        segment.map(([lng, lat]) => [lng, lat] as [number, number]),
                        0.95,
                    ));
                }
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
                const pickupLngLat = pickupPts.map((p) => [p.lng, p.lat] as [number, number]);
                overlayStrokes.push(...strokesFromLngLatPath(pickupLngLat, pickupApprox ? 0.55 : 0.95));
                map.addSource(PICKUP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: routeLineData(pickupLngLat),
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
                const tripLngLat = tripPts.map((p) => [p.lng, p.lat] as [number, number]);
                overlayStrokes.push(...strokesFromLngLatPath(tripLngLat, 0.95));
                map.addSource(TRIP_TRAIL_SOURCE_ID, {
                    type: "geojson",
                    data: routeLineData(tripLngLat),
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
            if (!hasRouteGeometry && locationPts.length > 1) {
                const locationLngLat = locationPts.map((p) => [p.lng, p.lat] as [number, number]);
                overlayStrokes.push(...strokesFromLngLatPath(locationLngLat, 0.9));
                map.addSource(ACTUAL_SOURCE_ID, {
                    type: "geojson",
                    data: routeLineData(locationLngLat),
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
            if (!suppressStraightFallback && !hasPlannedTrail && !hasRouteGeometry && locationPts.length < 2) {
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
                for (const seg of straightGradient) {
                    overlayStrokes.push({
                        coords: seg.coordinates.map(([lat, lng]) => [lng, lat] as [number, number]),
                        color: seg.color,
                        opacity: 0.9,
                    });
                }
            }

            } catch (err) {
                console.error("ride route GL layers failed; SVG overlay will still draw", err);
            }

            restackRouteLayers(map);
            syncRouteSvgOverlay(map, overlayStrokes);

            if (!fit) return;
            // Camera math uses the canvas size at call time. The modal can
            // mount this map before the 280px panel has a non-zero box, which
            // leaves the default zoom-13 view — about 3 km tall, so an 8 km
            // pickup→dropoff no longer fits. Resize first; skip until the
            // container is actually measurable (ResizeObserver retries).
            map.resize();
            const box = map.getContainer();
            if (box.clientWidth < 2 || box.clientHeight < 2) return;
            const plottable = (p: { lat: number; lng: number }) =>
                Number.isFinite(p.lat) && Number.isFinite(p.lng)
                && Math.abs(p.lat) <= 90 && Math.abs(p.lng) <= 180;
            // Fit bounds over every point we actually drew.
            const allPoints: { lat: number; lng: number }[] = [
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                ...pickupPts,
                ...tripPts,
                ...plannedPts,
                ...actualGeometry.coordinates.reduce<{ lat: number; lng: number }[]>(
                    (points, segment) => points.concat(segment.map(([lng, lat]) => ({ lat, lng }))),
                    [],
                ),
                ...(!hasRouteGeometry ? locationPts : []),
            ].filter(plottable);
            if (allPoints.length < 1) return;
            // Extra top padding reserves the status banner's footprint. Markers
            // anchor at their centre, so a 22px pin overhangs its own coordinate
            // by ~11px — with a flat 40px the topmost pin could sit underneath
            // the very banner that exists to keep it visible.
            const padding = { top: 52, bottom: 40, left: 40, right: 40 };
            if (box.clientHeight <= padding.top + padding.bottom
                || box.clientWidth <= padding.left + padding.right) {
                return;
            }
            fitBoundsToPoints(map, allPoints, padding);
            syncRouteSvgOverlay(map, overlayStrokes);
        };

        const map = mapRef.current;
        if (map && map.isStyleLoaded()) drawRef.current(map, true);
    }, [
        pickupLat, pickupLng, dropoffLat, dropoffLng,
        locationTrail, pickupTrail, pickupApprox, tripTrail, plannedTrail,
        actualGeometry, suppressStraightFallback,
    ]);

    // ── Create the map ────────────────────────────────────────────────────────
    // Depends on the pickup/dropoff primitives only. Route data is *not* a
    // dependency here — see drawRef above for why that matters.
    useEffect(() => {
        // No usable WebGL means never build a MapLibre map at all — it would
        // only ever produce a blank canvas.
        if (!containerRef.current || !webglOk) return;

        const chain = basemapChain();
        let disposed = false;
        let detach: (() => void) | null = null;
        let current: maplibregl.Map | null = null;

        const build = (attempt: number) => {
            if (disposed || !containerRef.current) return;

            const map = new maplibregl.Map({
                container: containerRef.current,
                style: chain[attempt],
                center: [(pickupLng + dropoffLng) / 2, (pickupLat + dropoffLat) / 2],
                zoom: 13,
                // Static-summary view — no zoom buttons, just a compact
                // attribution badge so the map stays distraction-free.
                attributionControl: { compact: true },
            });
            current = map;
            mapRef.current = map;

            // Pickup / dropoff pins are DOM overlays, not style layers, so they
            // render without a single tile. Adding them here rather than inside
            // on("load") is deliberate: MapLibre fires `load` only once the
            // style *and every source* finish, so a basemap that never completes
            // used to hide the pins and the route entirely — on the screen used
            // for SGI and dispute review. The forensic content must not depend
            // on a third-party tile host being up.
            new maplibregl.Marker({
                element: makeRoutePinEl({ kind: "pickup", size: 22, title: "Pickup" }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Pickup"))
                .addTo(map);

            new maplibregl.Marker({
                element: makeRoutePinEl({ kind: "dropoff", size: 22, title: "Dropoff" }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Dropoff"))
                .addTo(map);

            // Route layers go on as soon as the *style* is parsed, which happens
            // well before (and independently of) tile delivery.
            let fitted = false;
            let drawing = false;
            const redrawOverlay = () => {
                const strokes = overlayStrokesByMap.get(map);
                if (strokes) syncRouteSvgOverlay(map, strokes);
            };
            const drawWhenReady = () => {
                if (disposed || drawing || !map.isStyleLoaded()) return;
                drawing = true;
                try {
                    // addLayer/moveLayer emit styledata. Re-running a full
                    // clear+add on that event would wipe the line every frame
                    // (DOM pins would still show). Once the camera has fitted,
                    // only restack and reproject the SVG overlay.
                    const overlayReady = (overlayStrokesByMap.get(map)?.length ?? 0) > 0;
                    if (fitted && (hasRouteLayer(map) || overlayReady)) {
                        restackRouteLayers(map);
                        redrawOverlay();
                    } else {
                        drawRef.current(map, !fitted);
                    }
                    const box = map.getContainer();
                    if (box.clientWidth >= 2 && box.clientHeight >= 2) fitted = true;
                } finally {
                    drawing = false;
                }
            };
            map.on("styledata", drawWhenReady);
            map.on("load", drawWhenReady);
            map.on("move", redrawOverlay);
            map.on("moveend", redrawOverlay);
            map.on("idle", redrawOverlay);
            map.on("resize", redrawOverlay);
            drawWhenReady();

            detach = attachBasemapFallback(map, chain, attempt, {
                // Clears the "trying another provider…" banner once a later hop
                // actually renders. Without this the banner is write-only:
                // onRetry sets it and nothing ever unsets it, so a successful
                // fallback still leaves "Basemap slow to load" pinned over a
                // working map until the modal is closed.
                onLoaded: () => {
                    if (disposed) return;
                    setBasemapStatus("ok");
                },
                onRetry: (_next, nextAttempt) => {
                    if (disposed) return;
                    detach?.();
                    detach = null;
                    map.remove();
                    if (mapRef.current === map) mapRef.current = null;
                    setBasemapStatus("retrying");
                    build(nextAttempt);
                },
                onExhausted: () => {
                    if (disposed) return;
                    // Keep the last map: its pins and route are already drawn,
                    // and a background-less route still answers the question the
                    // admin opened this panel to ask.
                    setBasemapStatus("failed");
                },
            });
        };

        setBasemapStatus("ok");
        build(0);

        let lastW = 0;
        let lastH = 0;
        const ro = new ResizeObserver(() => {
            const map = current;
            const el = containerRef.current;
            if (disposed || !map || !el) return;
            const w = el.clientWidth;
            const h = el.clientHeight;
            if (w < 2 || h < 2 || (w === lastW && h === lastH)) return;
            lastW = w;
            lastH = h;
            map.resize();
            if (map.isStyleLoaded()) drawRef.current(map, true);
        });
        if (containerRef.current) ro.observe(containerRef.current);

        return () => {
            disposed = true;
            ro.disconnect();
            detach?.();
            current?.remove();
            mapRef.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, webglOk]);

    return (
        <div className="relative w-full h-[280px] rounded-xl overflow-hidden">
            {useStatic ? (
                <StaticRouteMap
                    pickupLat={pickupLat}
                    pickupLng={pickupLng}
                    dropoffLat={dropoffLat}
                    dropoffLng={dropoffLng}
                    paths={suppressStraightFallback && staticPaths?.length === 0 ? [] : staticPaths}
                />
            ) : (
                <div
                    ref={containerRef}
                    className="h-full w-full"
                    style={{ height: "100%", width: "100%" }}
                />
            )}
            {/* Only while MapLibre is still failing over. Once the chain is
                exhausted we hand off to StaticRouteMap, which draws the route
                and pins with no WebGL — so the old "basemap unavailable"
                wording would have been untrue there. Since 2026-09-14 that
                renderer may legitimately have no basemap at all (Carto was its
                third-party default and was removed), but it raises its own
                notice for both the blocked and the unconfigured case, so
                suppressing this band there still leaves the admin told. */}
            {basemapStatus === "retrying" && !useStatic && (
                // bg-background (not /90), matching monitoring-map.tsx's demand
                // legend: a translucent panel over map content puts muted text
                // right at the contrast floor with what's underneath unknowable
                // — and here "underneath" is the route gradient's saturated
                // orange/red.
                //
                // Neutral (not text-destructive like heat-map.tsx /
                // monitoring-map.tsx use for the same "chain exhausted"
                // condition) on purpose: there the map is the whole panel and a
                // failed basemap means nothing renders, so it is an error. Here
                // the pins and route still draw, so only the backdrop is
                // missing — alarm-red would overstate what the admin lost.
                // Full opacity rather than the sibling emptyHint's /70
                // (ride-detail-modal.tsx) because muted-on-card already measures
                // ~4.8:1 in the light theme; /70 would push it under AA.
                <div
                    role="status"
                    className="absolute inset-x-0 top-0 z-10 border-b border-border bg-background px-3 py-1.5 text-[10px] text-muted-foreground"
                >
                    Basemap slow to load — trying another provider…
                </div>
            )}
        </div>
    );
}
