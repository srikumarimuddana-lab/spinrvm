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

            // Planned route (dashed)
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

            // Fit bounds
            const allPoints: { lat: number; lng: number }[] = [
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                ...(locationTrail ?? []).map((p) => ({ lat: p.lat, lng: p.lng })),
            ];
            fitBoundsToPoints(map, allPoints, 40);
        });

        return () => {
            map.remove();
            mapRef.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail]);

    return <div ref={containerRef} className="w-full h-[280px] rounded-xl overflow-hidden" />;
}
