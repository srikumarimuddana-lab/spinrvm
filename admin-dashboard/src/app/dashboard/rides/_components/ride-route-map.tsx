"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_URL,
    fitBoundsToPoints,
    makeCircleMarkerEl,
} from "@/lib/map/maplibre-base";

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    locationTrail?: { lat: number; lng: number; timestamp?: string }[];
}

const PLANNED_SOURCE_ID = "ride-planned-src";
const PLANNED_LAYER_ID = "ride-planned-lyr";
const ACTUAL_SOURCE_ID = "ride-actual-src";
const ACTUAL_LAYER_ID = "ride-actual-lyr";

// OSRM public routing endpoint (free, OpenStreetMap). Returns a GeoJSON
// LineString snapped to the road network for the driving profile.
// NOTE: demo server is best-effort — on rate-limit or network failure we
// silently fall back to the straight line, so the UI never breaks.
const OSRM_ENDPOINT = "https://router.project-osrm.org/route/v1/driving";

async function fetchRoadRoute(
    pickup: { lat: number; lng: number },
    dropoff: { lat: number; lng: number },
    signal: AbortSignal,
): Promise<Array<[number, number]> | null> {
    const url =
        `${OSRM_ENDPOINT}/` +
        `${pickup.lng},${pickup.lat};${dropoff.lng},${dropoff.lat}` +
        `?overview=full&geometries=geojson`;
    try {
        const res = await fetch(url, { signal });
        if (!res.ok) return null;
        const data = await res.json();
        const coords = data?.routes?.[0]?.geometry?.coordinates;
        if (Array.isArray(coords) && coords.length >= 2) {
            return coords as Array<[number, number]>;
        }
        return null;
    } catch {
        // AbortError / network / JSON issues → silent fallback to straight line
        return null;
    }
}

export default function RideRouteMap({ pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [(pickupLng + dropoffLng) / 2, (pickupLat + dropoffLat) / 2],
            zoom: 13,
            // Mirror the original Leaflet map which disabled zoom controls
            // for this static-summary view.
            attributionControl: { compact: true },
        });
        mapRef.current = map;

        // OSRM fetch is async and raced against unmount; signal lets us
        // abort in-flight if the user closes the drawer before it returns.
        const abortCtrl = new AbortController();

        map.on("load", () => {
            // Pickup marker (green)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#10b981", size: 16 }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (blue)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#3b82f6", size: 16 }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Dropoff"))
                .addTo(map);

            // Planned route. Start with the straight-line fallback so the
            // map always shows *something* immediately; then upgrade to the
            // road-snapped OSRM geometry when the fetch resolves.
            map.addSource(PLANNED_SOURCE_ID, {
                type: "geojson",
                data: {
                    type: "Feature",
                    properties: {},
                    geometry: {
                        type: "LineString",
                        coordinates: [[pickupLng, pickupLat], [dropoffLng, dropoffLat]],
                    },
                },
            });
            map.addLayer({
                id: PLANNED_LAYER_ID,
                type: "line",
                source: PLANNED_SOURCE_ID,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": "#9ca3af",
                    "line-width": 2,
                    "line-opacity": 0.6,
                    "line-dasharray": ["literal", [2, 2]],
                },
            });

            // Road-snapped upgrade — replace the straight-line source with
            // OSRM geometry when it arrives. Re-fit bounds to the new route
            // so the zoom level matches the actual driven area. On failure
            // we keep the straight line (user still sees something).
            fetchRoadRoute(
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                abortCtrl.signal,
            ).then((coords) => {
                if (!coords || !mapRef.current) return;
                const src = map.getSource(PLANNED_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
                if (!src) return;
                src.setData({
                    type: "Feature",
                    properties: {},
                    geometry: { type: "LineString", coordinates: coords },
                });
                // Solid line once it's a real road route — dashed was a
                // "this is just a straight-line estimate" hint.
                if (map.getLayer(PLANNED_LAYER_ID)) {
                    map.setPaintProperty(PLANNED_LAYER_ID, "line-dasharray", [1]);
                    map.setPaintProperty(PLANNED_LAYER_ID, "line-opacity", 0.8);
                    map.setPaintProperty(PLANNED_LAYER_ID, "line-width", 3);
                }
                // Re-fit bounds to the actual route geometry (+ existing trail points)
                const routePoints = coords.map(([lng, lat]) => ({ lat, lng }));
                const allPoints: { lat: number; lng: number }[] = [
                    ...routePoints,
                    ...(locationTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
                ];
                fitBoundsToPoints(map, allPoints, 40);
            });

            // Actual route (solid blue) — only if trail is provided
            if (locationTrail && locationTrail.length > 1) {
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

            // Initial bounds fit (pre-OSRM) so the user isn't staring at a
            // world map while the route fetch is in flight.
            const allPoints: { lat: number; lng: number }[] = [
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                ...(locationTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
            ];
            fitBoundsToPoints(map, allPoints, 40);
        });

        return () => {
            abortCtrl.abort();
            map.remove();
            mapRef.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail]);

    return <div ref={containerRef} className="w-full h-[280px] rounded-xl overflow-hidden" />;
}
