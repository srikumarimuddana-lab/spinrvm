"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import { Crosshair, Maximize2 } from "lucide-react";
import { MAP_STYLE_URL, addStandardControls, makeCircleMarkerEl } from "@/lib/map/maplibre-base";

export interface LatLng {
    lat: number;
    lng: number;
}
export interface VenueMapPoint extends LatLng {
    name: string;
}

interface VenueMapProps {
    center: LatLng;
    radiusM: number;
    points: VenueMapPoint[];
    /** Index of the pickup point being edited, or null to edit the venue centre. */
    selectedIndex: number | null;
    onCenterChange: (lat: number, lng: number) => void;
    onPointChange: (index: number, lat: number, lng: number) => void;
    /** Called when a marker is clicked, so the parent can sync its selected row. */
    onSelect?: (index: number | null) => void;
    height?: string;
}

const CIRCLE_SRC = "venue-radius-src";
const CIRCLE_FILL = "venue-radius-fill";
const CIRCLE_LINE = "venue-radius-line";

const CENTER_COLOR = "#7c3aed";
const POINT_COLOR = "#0ea5e9";
const SELECTED_COLOR = "#f59e0b";

/**
 * A circle of `radiusM` metres as a GeoJSON polygon.
 *
 * MapLibre's `circle` layer sizes in screen pixels, so it would grow and shrink
 * against the map as the admin zooms — useless for judging whether a rider's pin
 * actually falls inside the detection radius. This traces the real ground
 * distance instead, so what is drawn is what `/maps/pickup-points` will match.
 */
function circlePolygon(lat: number, lng: number, radiusM: number, steps = 96): GeoJSON.FeatureCollection {
    if (!Number.isFinite(lat) || !Number.isFinite(lng) || !(radiusM > 0)) {
        return { type: "FeatureCollection", features: [] };
    }
    const dLat = radiusM / 111_320;
    // Longitude degrees shrink toward the poles; clamp so a bad latitude can
    // never divide by ~0 and produce a circle that wraps the globe.
    const dLng = radiusM / (111_320 * Math.max(Math.cos((lat * Math.PI) / 180), 0.01));
    const ring: GeoJSON.Position[] = [];
    for (let i = 0; i <= steps; i++) {
        const t = (i / steps) * 2 * Math.PI;
        ring.push([lng + dLng * Math.cos(t), lat + dLat * Math.sin(t)]);
    }
    return {
        type: "FeatureCollection",
        features: [{ type: "Feature", properties: {}, geometry: { type: "Polygon", coordinates: [ring] } }],
    };
}

export default function VenueMap({
    center,
    radiusM,
    points,
    selectedIndex,
    onCenterChange,
    onPointChange,
    onSelect,
    height = "380px",
}: VenueMapProps) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const centerMarkerRef = useRef<maplibregl.Marker | null>(null);
    const pointMarkersRef = useRef<maplibregl.Marker[]>([]);
    const [styleFailed, setStyleFailed] = useState(false);

    // Current geometry, readable from map event handlers registered once on mount.
    const geomRef = useRef({ lat: center.lat, lng: center.lng, radiusM });
    useEffect(() => {
        geomRef.current = { lat: center.lat, lng: center.lng, radiusM };
    }, [center.lat, center.lng, radiusM]);

    // Handlers and live values are mirrored into refs because the map's own
    // event listeners are registered once on mount and would otherwise close
    // over the first render's props forever.
    const selectedRef = useRef(selectedIndex);
    const cbRef = useRef({ onCenterChange, onPointChange, onSelect });
    useEffect(() => {
        selectedRef.current = selectedIndex;
        cbRef.current = { onCenterChange, onPointChange, onSelect };
    }, [selectedIndex, onCenterChange, onPointChange, onSelect]);

    const validCenter = Number.isFinite(center.lat) && Number.isFinite(center.lng) && (center.lat !== 0 || center.lng !== 0);

    /** Zoom to fit the detection circle and every pickup point. */
    const fitAll = useCallback(() => {
        const map = mapRef.current;
        if (!map || !validCenter) return;
        const bounds = new maplibregl.LngLatBounds();
        const dLat = radiusM / 111_320;
        const dLng = radiusM / (111_320 * Math.max(Math.cos((center.lat * Math.PI) / 180), 0.01));
        bounds.extend([center.lng - dLng, center.lat - dLat]);
        bounds.extend([center.lng + dLng, center.lat + dLat]);
        for (const p of points) {
            if (Number.isFinite(p.lat) && Number.isFinite(p.lng) && (p.lat !== 0 || p.lng !== 0)) {
                bounds.extend([p.lng, p.lat]);
            }
        }
        map.fitBounds(bounds, { padding: 60, maxZoom: 18, duration: 400 });
    }, [center.lat, center.lng, radiusM, points, validCenter]);

    // ── Map init (once) ──────────────────────────────────────────────
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;
        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [validCenter ? center.lng : -106.67, validCenter ? center.lat : 52.13],
            zoom: validCenter ? 15 : 11,
        });
        addStandardControls(map);
        mapRef.current = map;

        // Idempotent, so it can run on both `load` and every `styledata`.
        // Adding it only on `load` meant a style that failed to fetch — a
        // blocked tile host, a flaky CDN — left the admin with markers but no
        // radius circle, silently, which is the one thing the map is for.
        const ensureRadiusLayer = () => {
            if (!map.isStyleLoaded() || map.getSource(CIRCLE_SRC)) return;
            const { lat, lng, radiusM: r } = geomRef.current;
            map.addSource(CIRCLE_SRC, { type: "geojson", data: circlePolygon(lat, lng, r) });
            map.addLayer({
                id: CIRCLE_FILL,
                type: "fill",
                source: CIRCLE_SRC,
                paint: { "fill-color": CENTER_COLOR, "fill-opacity": 0.12 },
            });
            map.addLayer({
                id: CIRCLE_LINE,
                type: "line",
                source: CIRCLE_SRC,
                paint: { "line-color": CENTER_COLOR, "line-width": 2, "line-dasharray": [2, 1] },
            });
            setStyleFailed(false);
        };

        map.on("load", ensureRadiusLayer);
        map.on("styledata", ensureRadiusLayer);
        map.on("error", (e) => {
            // Surface a failed basemap instead of rendering a plausible-looking
            // but incomplete map: coordinates stay correct, the circle does not.
            if (String((e as any)?.error?.message || "").includes("style") || !map.isStyleLoaded()) {
                setStyleFailed(true);
            }
        });

        // Click places whichever target is currently selected: a pickup point
        // if one is being edited, otherwise the venue centre.
        map.on("click", (e) => {
            const { lat, lng } = e.lngLat;
            const idx = selectedRef.current;
            if (idx === null) cbRef.current.onCenterChange(lat, lng);
            else cbRef.current.onPointChange(idx, lat, lng);
        });

        return () => {
            map.remove();
            mapRef.current = null;
            centerMarkerRef.current = null;
            pointMarkersRef.current = [];
        };
        // Init only — subsequent prop changes are handled by the effects below.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // ── Radius circle follows centre + radius ────────────────────────
    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;
        const src = map.getSource(CIRCLE_SRC) as maplibregl.GeoJSONSource | undefined;
        src?.setData(circlePolygon(center.lat, center.lng, radiusM));
    }, [center.lat, center.lng, radiusM]);

    // ── Centre marker ────────────────────────────────────────────────
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !validCenter) return;
        if (!centerMarkerRef.current) {
            const el = makeCircleMarkerEl({
                color: CENTER_COLOR,
                size: 22,
                title: "Venue centre — drag to move",
            });
            const marker = new maplibregl.Marker({ element: el, draggable: true })
                .setLngLat([center.lng, center.lat])
                .addTo(map);
            marker.on("dragend", () => {
                const { lat, lng } = marker.getLngLat();
                cbRef.current.onCenterChange(lat, lng);
            });
            el.addEventListener("click", (ev) => {
                ev.stopPropagation();
                cbRef.current.onSelect?.(null);
            });
            centerMarkerRef.current = marker;
        } else {
            centerMarkerRef.current.setLngLat([center.lng, center.lat]);
        }
    }, [center.lat, center.lng, validCenter]);

    // ── Pickup-point markers ─────────────────────────────────────────
    // Rebuilt when the count or the selection changes (both alter what each
    // marker looks like); otherwise moved in place, so a drag is never
    // interrupted by the re-render its own dragend triggers.
    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;

        const positioned = points.map((p, i) => ({ p, i })).filter(
            ({ p }) => Number.isFinite(p.lat) && Number.isFinite(p.lng) && (p.lat !== 0 || p.lng !== 0),
        );

        if (pointMarkersRef.current.length !== positioned.length) {
            for (const m of pointMarkersRef.current) m.remove();
            pointMarkersRef.current = positioned.map(({ p, i }) => {
                const selected = i === selectedIndex;
                const el = makeCircleMarkerEl({
                    color: selected ? SELECTED_COLOR : POINT_COLOR,
                    size: selected ? 26 : 20,
                    label: String(i + 1),
                    title: `${p.name || `Point ${i + 1}`} — drag to move`,
                });
                const marker = new maplibregl.Marker({ element: el, draggable: true })
                    .setLngLat([p.lng, p.lat])
                    .addTo(map);
                marker.on("dragend", () => {
                    const { lat, lng } = marker.getLngLat();
                    cbRef.current.onPointChange(i, lat, lng);
                });
                el.addEventListener("click", (ev) => {
                    ev.stopPropagation();
                    cbRef.current.onSelect?.(i);
                });
                return marker;
            });
            return;
        }

        positioned.forEach(({ p, i }, slot) => {
            const marker = pointMarkersRef.current[slot];
            marker.setLngLat([p.lng, p.lat]);
            const el = marker.getElement();
            const selected = i === selectedIndex;
            el.style.backgroundColor = selected ? SELECTED_COLOR : POINT_COLOR;
            el.style.width = el.style.height = selected ? "26px" : "20px";
            el.title = `${p.name || `Point ${i + 1}`} — drag to move`;
        });
    }, [points, selectedIndex]);

    const target =
        selectedIndex === null
            ? "the venue centre"
            : `“${points[selectedIndex]?.name || `Point ${selectedIndex + 1}`}”`;

    return (
        <div className="space-y-2">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <p className="text-xs text-muted-foreground">
                    Click the map or drag a marker to move <span className="font-medium text-foreground">{target}</span>.
                    {selectedIndex === null && points.length > 0 && " Select a pickup point below to place it instead."}
                </p>
                <div className="flex items-center gap-1.5">
                    <button
                        type="button"
                        onClick={fitAll}
                        disabled={!validCenter}
                        className="inline-flex items-center gap-1 text-xs border rounded-lg px-2.5 py-1.5 hover:bg-muted disabled:opacity-50"
                    >
                        <Maximize2 className="h-3.5 w-3.5" /> Fit to venue
                    </button>
                    <button
                        type="button"
                        onClick={() => mapRef.current?.flyTo({ center: [center.lng, center.lat], zoom: 17, duration: 400 })}
                        disabled={!validCenter}
                        className="inline-flex items-center gap-1 text-xs border rounded-lg px-2.5 py-1.5 hover:bg-muted disabled:opacity-50"
                    >
                        <Crosshair className="h-3.5 w-3.5" /> Zoom to centre
                    </button>
                </div>
            </div>
            <div ref={containerRef} style={{ height }} className="w-full rounded-lg overflow-hidden border" />
            {styleFailed && (
                <p className="text-xs text-destructive">
                    Base map tiles failed to load. Marker positions and the coordinates below are still
                    correct, but the detection-radius circle and imagery are unavailable — don&apos;t use
                    this view to judge whether a point falls inside the radius until it recovers.
                </p>
            )}
            {!validCenter && (
                <p className="text-xs text-amber-600 dark:text-amber-500">
                    No centre set yet — click the map to place this venue.
                </p>
            )}
            <div className="flex items-center gap-4 text-[11px] text-muted-foreground">
                <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: CENTER_COLOR }} /> Centre + detection radius
                </span>
                <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: POINT_COLOR }} /> Pickup point
                </span>
                <span className="inline-flex items-center gap-1.5">
                    <span className="h-2.5 w-2.5 rounded-full" style={{ backgroundColor: SELECTED_COLOR }} /> Selected
                </span>
            </div>
        </div>
    );
}
