"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_URL,
    fitBoundsToPoints,
    makeCircleMarkerEl,
} from "@/lib/map/maplibre-base";
import {
    buildStraightRouteGradient,
    ROUTE_MARKER_SIZE,
    ROUTE_PIN_COLORS,
    ROUTE_STROKE_WIDTH,
} from "@spinr/shared/constants/routeMapStyle";

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    /** Optional completion point (where the ride actually ended, if it differs
     *  from the booked dropoff). Rendered as an amber marker when provided. */
    completionLat?: number | null;
    completionLng?: number | null;
}

// One straight pickup→destination route, drawn as an orange→red gradient
// (uniform spec — see @spinr/shared/constants/routeMapStyle). The gradient is
// faked with per-segment coloured LineString features.
const ROUTE_SOURCE_ID = "ride-route-src";
const ROUTE_LAYER_ID = "ride-route-lyr";

export default function RideRouteMap({
    pickupLat,
    pickupLng,
    dropoffLat,
    dropoffLng,
    completionLat,
    completionLng,
}: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const hasCompletion = completionLat != null && completionLng != null;

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
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.pickup, size: ROUTE_MARKER_SIZE }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (red)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.dropoff, size: ROUTE_MARKER_SIZE }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Dropoff"))
                .addTo(map);

            // Completion marker (amber) — only when a distinct completion point exists.
            if (hasCompletion) {
                new maplibregl.Marker({
                    element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.completion, size: ROUTE_MARKER_SIZE }),
                })
                    .setLngLat([completionLng!, completionLat!])
                    .setPopup(new maplibregl.Popup({ closeButton: false, offset: 6 }).setText("Completed"))
                    .addTo(map);
            }

            // Single straight pickup→dropoff route as an orange→red gradient.
            // GeoJSON is [lng, lat]; the shared helper returns [lat, lng], so swap.
            const segments = buildStraightRouteGradient(
                [pickupLat, pickupLng],
                [dropoffLat, dropoffLng],
            );
            if (segments.length > 0) {
                map.addSource(ROUTE_SOURCE_ID, {
                    type: "geojson",
                    data: {
                        type: "FeatureCollection",
                        features: segments.map((seg) => ({
                            type: "Feature",
                            properties: { color: seg.color },
                            geometry: {
                                type: "LineString",
                                coordinates: seg.coordinates.map(([lat, lng]) => [lng, lat]),
                            },
                        })),
                    },
                });
                map.addLayer({
                    id: ROUTE_LAYER_ID,
                    type: "line",
                    source: ROUTE_SOURCE_ID,
                    layout: { "line-cap": "round", "line-join": "round" },
                    paint: {
                        "line-color": ["get", "color"],
                        "line-width": ROUTE_STROKE_WIDTH,
                        "line-opacity": 0.9,
                    },
                });
            }

            const allPoints: { lat: number; lng: number }[] = [
                { lat: pickupLat, lng: pickupLng },
                { lat: dropoffLat, lng: dropoffLng },
                ...(hasCompletion ? [{ lat: completionLat!, lng: completionLng! }] : []),
            ];
            fitBoundsToPoints(map, allPoints, 40);
        });

        return () => {
            map.remove();
            mapRef.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, completionLat, completionLng]);

    return <div ref={containerRef} className="w-full h-[280px] rounded-xl overflow-hidden" />;
}
