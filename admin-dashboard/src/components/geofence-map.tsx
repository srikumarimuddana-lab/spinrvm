"use client";

import { useEffect, useRef, useCallback } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import MapboxDraw from "@mapbox/mapbox-gl-draw";
import "@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css";
import {
    MAP_STYLE_URL,
    addStandardControls,
    fitBoundsToPoints,
    polygonPointsToGeoJSON,
} from "@/lib/map/maplibre-base";

interface PolygonPoint {
    lat: number;
    lng: number;
}

interface GeofenceMapProps {
    polygon?: PolygonPoint[];
    center?: PolygonPoint;
    zoom?: number;
    onPolygonChange?: (polygon: PolygonPoint[]) => void;
    readonly?: boolean;
    height?: string;
}

const READONLY_SOURCE_ID = "geofence-readonly-src";
const READONLY_FILL_LAYER_ID = "geofence-readonly-fill";
const READONLY_LINE_LAYER_ID = "geofence-readonly-line";

/**
 * @mapbox/mapbox-gl-draw is built against Mapbox GL JS. Two things
 * could make it misbehave against MapLibre:
 *
 *   1. Class-name mismatch — mapbox-gl-draw emits elements with
 *      .mapboxgl-ctrl-* classes and its own bundled CSS targets
 *      those, so we DO NOT rewrite the class names here (doing so
 *      silently breaks button styling). MapLibre only styles the
 *      outer positioning container (.maplibregl-ctrl-top-left
 *      etc.), which is unaffected.
 *
 *   2. Method surface — newer MapLibre renamed a couple of internal
 *      helpers (e.g. getLayer's lookup order). Current MapLibre 3+
 *      preserves every method mapbox-gl-draw reads.
 *
 * If a future MapLibre bump breaks this, add specific patches here.
 * For now the shim is a no-op marker — imports of the draw CSS at
 * the top of the file do all the real work.
 */
function installMapLibreCompatShim() {
    void maplibregl;
}

function featureCollectionOfPolygon(points: PolygonPoint[]): GeoJSON.FeatureCollection {
    const geo = polygonPointsToGeoJSON(points);
    if (!geo) return { type: "FeatureCollection", features: [] };
    return {
        type: "FeatureCollection",
        features: [{ type: "Feature", properties: {}, geometry: geo }],
    };
}

function polygonFromDrawFeature(feature: GeoJSON.Feature): PolygonPoint[] {
    if (!feature || feature.geometry.type !== "Polygon") return [];
    const ring = feature.geometry.coordinates[0] ?? [];
    // Drop the closing point that GeoJSON Polygons include
    const open = ring.length > 0 &&
        ring[0][0] === ring[ring.length - 1][0] &&
        ring[0][1] === ring[ring.length - 1][1]
        ? ring.slice(0, -1)
        : ring;
    return open.map(([lng, lat]) => ({
        lat: parseFloat(lat.toFixed(6)),
        lng: parseFloat(lng.toFixed(6)),
    }));
}

export default function GeofenceMap({
    polygon,
    center,
    zoom = 12,
    onPolygonChange,
    readonly = false,
    height = "400px",
}: GeofenceMapProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const drawRef = useRef<any>(null);
    const isLoadedRef = useRef(false);

    const onPolygonChangeRef = useRef(onPolygonChange);
    useEffect(() => {
        onPolygonChangeRef.current = onPolygonChange;
    }, [onPolygonChange]);

    // Effective centre
    const effectiveCenter = center
        ? center
        : polygon && polygon.length > 0
            ? {
                lat: polygon.reduce((s, p) => s + p.lat, 0) / polygon.length,
                lng: polygon.reduce((s, p) => s + p.lng, 0) / polygon.length,
            }
            : { lat: 52.13, lng: -106.67 }; // Default: Saskatoon

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        // Apply the compat shim BEFORE constructing MapboxDraw.
        installMapLibreCompatShim();

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [effectiveCenter.lng, effectiveCenter.lat],
            zoom,
        });
        addStandardControls(map);
        mapRef.current = map;

        map.on("load", () => {
            isLoadedRef.current = true;

            if (readonly) {
                // Readonly mode: render the polygon via a standard
                // GeoJSON source + fill/line layers, no draw control.
                map.addSource(READONLY_SOURCE_ID, {
                    type: "geojson",
                    data: featureCollectionOfPolygon(polygon ?? []),
                });
                map.addLayer({
                    id: READONLY_FILL_LAYER_ID,
                    type: "fill",
                    source: READONLY_SOURCE_ID,
                    paint: { "fill-color": "#7c3aed", "fill-opacity": 0.2 },
                });
                map.addLayer({
                    id: READONLY_LINE_LAYER_ID,
                    type: "line",
                    source: READONLY_SOURCE_ID,
                    paint: { "line-color": "#7c3aed", "line-width": 2 },
                });
            } else {
                const draw = new MapboxDraw({
                    displayControlsDefault: false,
                    controls: { polygon: true, trash: true },
                });
                // Cast because mapbox-gl-draw's IControl type targets
                // mapbox-gl, not maplibre-gl — the runtime surface is
                // identical after the shim above.
                map.addControl(draw as unknown as maplibregl.IControl, "top-left");
                drawRef.current = draw;

                // Seed with existing polygon (if any)
                if (polygon && polygon.length >= 3) {
                    draw.set(featureCollectionOfPolygon(polygon));
                }

                // Events
                const onCreate = (e: { features: GeoJSON.Feature[] }) => {
                    // One polygon per geofence — drop any previously drawn
                    const featureToKeep = e.features[e.features.length - 1];
                    const allIds = (draw.getAll() as GeoJSON.FeatureCollection).features
                        .map((f) => f.id as string)
                        .filter((id) => id !== featureToKeep.id);
                    if (allIds.length > 0) draw.delete(allIds);
                    onPolygonChangeRef.current?.(polygonFromDrawFeature(featureToKeep));
                };
                const onUpdate = (e: { features: GeoJSON.Feature[] }) => {
                    if (e.features[0]) {
                        onPolygonChangeRef.current?.(polygonFromDrawFeature(e.features[0]));
                    }
                };
                const onDelete = () => {
                    onPolygonChangeRef.current?.([]);
                };
                map.on("draw.create", onCreate);
                map.on("draw.update", onUpdate);
                map.on("draw.delete", onDelete);
            }

            // Fit to initial polygon
            if (polygon && polygon.length >= 3) {
                fitBoundsToPoints(map, polygon, 40);
            }
        });

        // Resize fix for dialog mount timing
        const resizeTimer = setTimeout(() => mapRef.current?.resize(), 200);

        return () => {
            clearTimeout(resizeTimer);
            drawRef.current = null;
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Update polygon externally (e.g. preset selection)
    const updatePolygon = useCallback((newPolygon: PolygonPoint[]) => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;

        if (readonly) {
            const src = map.getSource(READONLY_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
            src?.setData(featureCollectionOfPolygon(newPolygon));
        } else {
            const draw = drawRef.current;
            if (!draw) return;
            draw.deleteAll();
            if (newPolygon.length >= 3) {
                draw.set(featureCollectionOfPolygon(newPolygon));
            }
        }
        if (newPolygon.length >= 3) {
            fitBoundsToPoints(map, newPolygon, 40);
        }
    }, [readonly]);

    useEffect(() => {
        if (polygon) updatePolygon(polygon);
    }, [polygon, updatePolygon]);

    return (
        <div
            ref={containerRef}
            style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }}
        />
    );
}
