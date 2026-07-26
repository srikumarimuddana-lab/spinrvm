"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_URL,
    addStandardControls,
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
    driverLat?: number;
    driverLng?: number;
    /** Accepted for backward compatibility with the live page; the route is now
     *  the uniform straight pickup→dropoff gradient, so the driver trail is no
     *  longer drawn. */
    trail?: { lat: number; lng: number }[];
}

// Single straight pickup→dropoff route as an orange→red gradient (uniform spec).
const ROUTE_SOURCE_ID = "live-route-src";
const ROUTE_LAYER_ID = "live-route-lyr";

export default function LiveRideMap({ pickupLat, pickupLng, dropoffLat, dropoffLng, driverLat, driverLng }: Props) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const driverMarkerRef = useRef<maplibregl.Marker | null>(null);
    const isLoadedRef = useRef(false);

    // Initialize map once
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [(pickupLng + dropoffLng) / 2, (pickupLat + dropoffLat) / 2],
            zoom: 13,
        });
        addStandardControls(map);
        mapRef.current = map;

        map.on("load", () => {
            isLoadedRef.current = true;

            // Pickup marker (green)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.pickup, size: ROUTE_MARKER_SIZE }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (red)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: ROUTE_PIN_COLORS.dropoff, size: ROUTE_MARKER_SIZE }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Dropoff"))
                .addTo(map);

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
            driverMarkerRef.current?.remove();
            driverMarkerRef.current = null;
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng]);

    // Update driver position
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;

        if (driverLat != null && driverLng != null && driverMarkerRef.current) {
            driverMarkerRef.current.setLngLat([driverLng, driverLat]);
        }
    }, [driverLat, driverLng]);

    return <div ref={containerRef} className="w-full h-full min-h-[400px]" />;
}
