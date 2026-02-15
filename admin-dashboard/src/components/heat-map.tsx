"use client";

import { useEffect, useRef, useCallback } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

// Import leaflet.heat dynamically
let heatLayer: any = null;
if (typeof window !== "undefined") {
    try {
        heatLayer = require("leaflet.heat");
    } catch (e) {
        console.warn("leaflet.heat not available");
    }
}

// Fix Leaflet default icon paths for webpack/next
delete (L.Icon.Default.prototype as any)._getIconUrl;
L.Icon.Default.mergeOptions({
    iconRetinaUrl:
        "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon-2x.png",
    iconUrl:
        "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-icon.png",
    shadowUrl:
        "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/images/marker-shadow.png",
});

export interface HeatMapPoint {
    lat: number;
    lng: number;
    intensity: number;
}

export interface HeatMapSettings {
    radius?: number;
    blur?: number;
    gradient?: { [key: number]: string };
    maxZoom?: number;
    max?: number;
}

interface HeatMapProps {
    pickupPoints?: HeatMapPoint[];
    dropoffPoints?: HeatMapPoint[];
    settings?: HeatMapSettings;
    center?: { lat: number; lng: number };
    zoom?: number;
    height?: string;
    showPickups?: boolean;
    showDropoffs?: boolean;
}

export default function HeatMap({
    pickupPoints = [],
    dropoffPoints = [],
    settings = {},
    center = { lat: 52.13, lng: -106.67 }, // Default: Saskatoon
    zoom = 12,
    height = "600px",
    showPickups = true,
    showDropoffs = true,
}: HeatMapProps) {
    const mapRef = useRef<L.Map | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const heatLayerRef = useRef<any>(null);
    const pickupLayerRef = useRef<any>(null);
    const dropoffLayerRef = useRef<any>(null);

    // Default gradient for heat map
    const defaultGradient = {
        0.0: "#00ff00", // Green - low intensity
        0.3: "#88ff00",
        0.5: "#ffff00", // Yellow - medium intensity
        0.7: "#ff8800",
        1.0: "#ff0000", // Red - high intensity
    };

    const gradient = settings.gradient || defaultGradient;

    // Convert points to leaflet.heat format [lat, lng, intensity]
    const toHeatPoints = (points: HeatMapPoint[]) => {
        return points.map((p) => [p.lat, p.lng, p.intensity || 0.5] as [number, number, number]);
    };

    useEffect(() => {
        if (!containerRef.current || mapRef.current || !heatLayer) return;

        const map = L.map(containerRef.current, {
            center: [center.lat, center.lng],
            zoom,
        });

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors",
            maxZoom: 19,
        }).addTo(map);

        mapRef.current = map;

        // Resize fix for dialog mount timing
        setTimeout(() => map.invalidateSize(), 200);

        return () => {
            map.remove();
            mapRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Update heat layers when points or visibility changes
    useEffect(() => {
        if (!mapRef.current || !heatLayer) return;

        const map = mapRef.current;

        // Remove existing heat layers
        if (pickupLayerRef.current) {
            map.removeLayer(pickupLayerRef.current);
            pickupLayerRef.current = null;
        }
        if (dropoffLayerRef.current) {
            map.removeLayer(dropoffLayerRef.current);
            dropoffLayerRef.current = null;
        }

        const heatOptions = {
            radius: settings.radius || 25,
            blur: settings.blur || 15,
            maxZoom: settings.maxZoom || 17,
            max: settings.max || 1,
            gradient,
        };

        // Add pickup heat layer
        if (showPickups && pickupPoints.length > 0) {
            const pickupHeat = heatLayer.layer(toHeatPoints(pickupPoints), {
                ...heatOptions,
                // Use blue/cyan gradient for pickups
                gradient: {
                    0.0: "#00ffff",
                    0.3: "#00aaff",
                    0.5: "#0066ff",
                    0.7: "#0000ff",
                    1.0: "#0000aa",
                },
            });
            pickupHeat.addTo(map);
            pickupLayerRef.current = pickupHeat;
        }

        // Add dropoff heat layer
        if (showDropoffs && dropoffPoints.length > 0) {
            const dropoffHeat = heatLayer.layer(toHeatPoints(dropoffPoints), heatOptions);
            dropoffHeat.addTo(map);
            dropoffLayerRef.current = dropoffHeat;
        }

        // Fit bounds if we have points
        const allPoints = [...(showPickups ? pickupPoints : []), ...(showDropoffs ? dropoffPoints : [])];
        if (allPoints.length > 0) {
            const bounds = L.latLngBounds(
                allPoints.map((p) => [p.lat, p.lng] as [number, number])
            );
            map.fitBounds(bounds.pad(0.1));
        }
    }, [pickupPoints, dropoffPoints, showPickups, showDropoffs, settings, gradient]);

    return (
        <div
            ref={containerRef}
            style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }}
        />
    );
}
