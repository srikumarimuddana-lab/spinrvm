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
                element: makeCircleMarkerEl({ color: "#10b981", size: 20 }),
            })
                .setLngLat([pickupLng, pickupLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Pickup"))
                .addTo(map);

            // Dropoff marker (blue)
            new maplibregl.Marker({
                element: makeCircleMarkerEl({ color: "#3b82f6", size: 20 }),
            })
                .setLngLat([dropoffLng, dropoffLat])
                .setPopup(new maplibregl.Popup({ closeButton: false, offset: 8 }).setText("Dropoff"))
                .addTo(map);

            // Planned route (dashed grey line)
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

            // Trail source + layer (actual driver path)
            map.addSource(TRAIL_SOURCE_ID, {
                type: "geojson",
                data: {
                    type: "Feature",
                    properties: {},
                    geometry: { type: "LineString", coordinates: [] },
                },
            });
            map.addLayer({
                id: TRAIL_LAYER_ID,
                type: "line",
                source: TRAIL_SOURCE_ID,
                layout: { "line-cap": "round", "line-join": "round" },
                paint: {
                    "line-color": "#3b82f6",
                    "line-width": 3,
                    "line-opacity": 0.7,
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

    // Update driver position and trail
    useEffect(() => {
        const map = mapRef.current;
        if (!map || !isLoadedRef.current) return;

        if (driverLat != null && driverLng != null && driverMarkerRef.current) {
            driverMarkerRef.current.setLngLat([driverLng, driverLat]);
        }

        if (trail && trail.length > 0) {
            const src = map.getSource(TRAIL_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
            src?.setData({
                type: "Feature",
                properties: {},
                geometry: {
                    type: "LineString",
                    coordinates: trail.map((p) => [p.lng, p.lat]),
                },
            });
        }
    }, [driverLat, driverLng, trail]);

    return <div ref={containerRef} className="w-full h-full min-h-[400px]" />;
}
