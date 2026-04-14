"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Pencil, Check, Trash2, X } from "lucide-react";
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

// Source + layer IDs for the committed polygon (display)
const POLY_SRC = "geofence-poly-src";
const POLY_FILL = "geofence-poly-fill";
const POLY_LINE = "geofence-poly-line";
const POLY_VERTICES_SRC = "geofence-vertices-src";
const POLY_VERTICES_LAYER = "geofence-vertices-lyr";

// Source + layer IDs for the IN-PROGRESS polygon while drawing
const DRAFT_SRC = "geofence-draft-src";
const DRAFT_FILL = "geofence-draft-fill";
const DRAFT_LINE = "geofence-draft-line";
const DRAFT_POINTS_SRC = "geofence-draft-points-src";
const DRAFT_POINTS_LAYER = "geofence-draft-points-lyr";

function pointsToGeoJSON(points: PolygonPoint[]): GeoJSON.FeatureCollection {
    const poly = polygonPointsToGeoJSON(points);
    return {
        type: "FeatureCollection",
        features: poly
            ? [{ type: "Feature", properties: {}, geometry: poly }]
            : [],
    };
}

function pointsToVerticesGeoJSON(points: PolygonPoint[]): GeoJSON.FeatureCollection {
    return {
        type: "FeatureCollection",
        features: points.map((p, i) => ({
            type: "Feature",
            properties: { index: i },
            geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        })),
    };
}

function lineStringGeoJSON(points: PolygonPoint[]): GeoJSON.FeatureCollection {
    if (points.length < 2) return { type: "FeatureCollection", features: [] };
    return {
        type: "FeatureCollection",
        features: [{
            type: "Feature",
            properties: {},
            geometry: {
                type: "LineString",
                coordinates: points.map((p) => [p.lng, p.lat]),
            },
        }],
    };
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
    const isLoadedRef = useRef(false);

    const [committed, setCommitted] = useState<PolygonPoint[]>(polygon ?? []);
    const [drawing, setDrawing] = useState(false);
    const [draft, setDraft] = useState<PolygonPoint[]>([]);

    const draftRef = useRef<PolygonPoint[]>([]);
    draftRef.current = draft;
    const drawingRef = useRef(false);
    drawingRef.current = drawing;
    const committedRef = useRef(committed);
    committedRef.current = committed;
    const draggingIndexRef = useRef<number | null>(null);

    const onPolygonChangeRef = useRef(onPolygonChange);
    useEffect(() => {
        onPolygonChangeRef.current = onPolygonChange;
    }, [onPolygonChange]);

    // Sync prop changes from parent
    useEffect(() => {
        if (polygon) setCommitted(polygon);
    }, [polygon]);

    // Effective centre
    const effectiveCenter = center
        ? center
        : committed.length > 0
            ? {
                lat: committed.reduce((s, p) => s + p.lat, 0) / committed.length,
                lng: committed.reduce((s, p) => s + p.lng, 0) / committed.length,
            }
            : { lat: 52.13, lng: -106.67 }; // Default: Saskatoon

    // ── Map init ─────────────────────────────────────────────────────
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

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

            // Committed polygon layers
            map.addSource(POLY_SRC, { type: "geojson", data: pointsToGeoJSON([]) });
            map.addLayer({
                id: POLY_FILL,
                type: "fill",
                source: POLY_SRC,
                paint: { "fill-color": "#7c3aed", "fill-opacity": 0.2 },
            });
            map.addLayer({
                id: POLY_LINE,
                type: "line",
                source: POLY_SRC,
                paint: { "line-color": "#7c3aed", "line-width": 2 },
            });

            // Committed vertices (shown only in edit mode when draggable)
            map.addSource(POLY_VERTICES_SRC, {
                type: "geojson",
                data: pointsToVerticesGeoJSON([]),
            });
            map.addLayer({
                id: POLY_VERTICES_LAYER,
                type: "circle",
                source: POLY_VERTICES_SRC,
                paint: {
                    "circle-radius": 6,
                    "circle-color": "#ffffff",
                    "circle-stroke-color": "#7c3aed",
                    "circle-stroke-width": 2,
                },
                layout: { visibility: "none" },
            });

            // Draft (while drawing) layers
            map.addSource(DRAFT_SRC, { type: "geojson", data: pointsToGeoJSON([]) });
            map.addLayer({
                id: DRAFT_FILL,
                type: "fill",
                source: DRAFT_SRC,
                paint: { "fill-color": "#f59e0b", "fill-opacity": 0.15 },
            });
            map.addSource(DRAFT_POINTS_SRC, {
                type: "geojson",
                data: lineStringGeoJSON([]),
            });
            map.addLayer({
                id: DRAFT_LINE,
                type: "line",
                source: DRAFT_POINTS_SRC,
                paint: {
                    "line-color": "#f59e0b",
                    "line-width": 2,
                    // MapLibre 5 requires literal arrays to be tagged.
                    "line-dasharray": ["literal", [2, 2]],
                },
            });
            map.addSource("__tmp_draft_pts", {
                type: "geojson",
                data: pointsToVerticesGeoJSON([]),
            });
            map.addLayer({
                id: DRAFT_POINTS_LAYER,
                type: "circle",
                source: "__tmp_draft_pts",
                paint: {
                    "circle-radius": 5,
                    "circle-color": "#f59e0b",
                    "circle-stroke-color": "#ffffff",
                    "circle-stroke-width": 2,
                },
            });

            // Fit to initial polygon
            if ((polygon?.length ?? 0) >= 3) {
                fitBoundsToPoints(map, polygon!, 40);
            }

            // ── Click handler: add point while drawing, OR start
            //    vertex drag when clicking a committed vertex
            map.on("click", (e) => {
                if (drawingRef.current) {
                    const { lng, lat } = e.lngLat;
                    setDraft((prev) => [
                        ...prev,
                        {
                            lat: parseFloat(lat.toFixed(6)),
                            lng: parseFloat(lng.toFixed(6)),
                        },
                    ]);
                }
            });

            // Double-click while drawing → finish
            map.on("dblclick", (e) => {
                if (!drawingRef.current) return;
                e.preventDefault();
                finishRef.current?.();
            });

            // ── Vertex drag for committed polygon ────────────────────
            map.on("mouseenter", POLY_VERTICES_LAYER, () => {
                map.getCanvas().style.cursor = "move";
            });
            map.on("mouseleave", POLY_VERTICES_LAYER, () => {
                if (!drawingRef.current) map.getCanvas().style.cursor = "";
            });
            map.on("mousedown", POLY_VERTICES_LAYER, (e) => {
                if (drawingRef.current || readonly) return;
                if (!e.features || e.features.length === 0) return;
                e.preventDefault();
                draggingIndexRef.current = (e.features[0].properties?.index as number) ?? null;
                map.on("mousemove", onVertexDrag);
                map.once("mouseup", onVertexDragEnd);
            });

            function onVertexDrag(ev: maplibregl.MapMouseEvent) {
                const idx = draggingIndexRef.current;
                if (idx == null) return;
                const { lng, lat } = ev.lngLat;
                setCommitted((prev) => {
                    const next = prev.slice();
                    next[idx] = {
                        lat: parseFloat(lat.toFixed(6)),
                        lng: parseFloat(lng.toFixed(6)),
                    };
                    return next;
                });
            }
            function onVertexDragEnd() {
                map.off("mousemove", onVertexDrag);
                draggingIndexRef.current = null;
                // Persist the edit to the parent
                onPolygonChangeRef.current?.(committedRef.current);
            }
        });

        // Resize fix for dialog mount timing
        const resizeTimer = setTimeout(() => mapRef.current?.resize(), 200);

        return () => {
            clearTimeout(resizeTimer);
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // ── Keep committed polygon + vertex layer data in sync ───────────
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;
        (map.getSource(POLY_SRC) as maplibregl.GeoJSONSource | undefined)
            ?.setData(pointsToGeoJSON(committed));
        (map.getSource(POLY_VERTICES_SRC) as maplibregl.GeoJSONSource | undefined)
            ?.setData(pointsToVerticesGeoJSON(committed));
        // Show vertex handles only when editable + not drawing
        map.setLayoutProperty(
            POLY_VERTICES_LAYER,
            "visibility",
            !readonly && !drawing && committed.length >= 3 ? "visible" : "none",
        );
    }, [committed, readonly, drawing]);

    // ── Keep draft layers in sync ────────────────────────────────────
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;
        (map.getSource(DRAFT_SRC) as maplibregl.GeoJSONSource | undefined)
            ?.setData(pointsToGeoJSON(draft));
        (map.getSource(DRAFT_POINTS_SRC) as maplibregl.GeoJSONSource | undefined)
            ?.setData(lineStringGeoJSON(draft));
        (map.getSource("__tmp_draft_pts") as maplibregl.GeoJSONSource | undefined)
            ?.setData(pointsToVerticesGeoJSON(draft));
    }, [draft]);

    // ── Controls ─────────────────────────────────────────────────────
    const finishRef = useRef<(() => void) | null>(null);

    const startDrawing = useCallback(() => {
        if (readonly) return;
        setDrawing(true);
        setDraft([]);
        const map = mapRef.current;
        if (map) map.getCanvas().style.cursor = "crosshair";
    }, [readonly]);

    const cancelDrawing = useCallback(() => {
        setDrawing(false);
        setDraft([]);
        const map = mapRef.current;
        if (map) map.getCanvas().style.cursor = "";
    }, []);

    const finishDrawing = useCallback(() => {
        const pts = draftRef.current;
        if (pts.length < 3) {
            // Not enough points — stay in draw mode, let the user keep clicking
            return;
        }
        setCommitted(pts);
        setDrawing(false);
        setDraft([]);
        const map = mapRef.current;
        if (map) map.getCanvas().style.cursor = "";
        onPolygonChangeRef.current?.(pts);
    }, []);
    finishRef.current = finishDrawing;

    const clearPolygon = useCallback(() => {
        setCommitted([]);
        setDraft([]);
        setDrawing(false);
        onPolygonChangeRef.current?.([]);
    }, []);

    // Update polygon externally (e.g. preset selection) — already handled
    // via the polygon-prop sync effect above. When the prop changes, the
    // committed state updates, its effect re-renders the source, and we
    // also refit bounds.
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;
        if (committed.length >= 3) {
            fitBoundsToPoints(map, committed, 40);
        }
    }, [committed.length]);

    return (
        <div style={{ position: "relative", height, width: "100%" }}>
            <div
                ref={containerRef}
                style={{
                    height: "100%",
                    width: "100%",
                    borderRadius: "8px",
                    overflow: "hidden",
                }}
            />
            {!readonly && (
                <div
                    style={{
                        position: "absolute",
                        top: 8,
                        left: 8,
                        display: "flex",
                        flexDirection: "column",
                        gap: 6,
                        zIndex: 5,
                    }}
                >
                    {!drawing && (
                        <button
                            type="button"
                            onClick={startDrawing}
                            title={committed.length >= 3 ? "Redraw polygon" : "Draw polygon"}
                            style={toolbarButtonStyle}
                        >
                            <Pencil size={14} />
                            <span>{committed.length >= 3 ? "Redraw" : "Draw"}</span>
                        </button>
                    )}
                    {drawing && (
                        <>
                            <button
                                type="button"
                                onClick={finishDrawing}
                                disabled={draft.length < 3}
                                title="Finish polygon"
                                style={{
                                    ...toolbarButtonStyle,
                                    opacity: draft.length < 3 ? 0.5 : 1,
                                    cursor: draft.length < 3 ? "not-allowed" : "pointer",
                                    background: "#059669",
                                    color: "#fff",
                                    borderColor: "#059669",
                                }}
                            >
                                <Check size={14} />
                                <span>Finish ({draft.length})</span>
                            </button>
                            <button
                                type="button"
                                onClick={cancelDrawing}
                                title="Cancel"
                                style={toolbarButtonStyle}
                            >
                                <X size={14} />
                                <span>Cancel</span>
                            </button>
                        </>
                    )}
                    {!drawing && committed.length >= 3 && (
                        <button
                            type="button"
                            onClick={clearPolygon}
                            title="Clear polygon"
                            style={{
                                ...toolbarButtonStyle,
                                color: "#b91c1c",
                                borderColor: "#fecaca",
                            }}
                        >
                            <Trash2 size={14} />
                            <span>Clear</span>
                        </button>
                    )}
                </div>
            )}
            {drawing && (
                <div
                    style={{
                        position: "absolute",
                        bottom: 8,
                        left: "50%",
                        transform: "translateX(-50%)",
                        background: "rgba(17,24,39,0.85)",
                        color: "#fff",
                        padding: "6px 12px",
                        borderRadius: 6,
                        fontSize: 12,
                        zIndex: 5,
                        pointerEvents: "none",
                    }}
                >
                    Click on the map to add points · double-click or press Finish
                    when done · 3 points minimum
                </div>
            )}
        </div>
    );
}

const toolbarButtonStyle: React.CSSProperties = {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    padding: "6px 10px",
    background: "#ffffff",
    border: "1px solid #d4d4d8",
    borderRadius: 6,
    fontSize: 12,
    fontWeight: 600,
    color: "#27272a",
    boxShadow: "0 1px 3px rgba(0,0,0,0.12)",
    cursor: "pointer",
};
