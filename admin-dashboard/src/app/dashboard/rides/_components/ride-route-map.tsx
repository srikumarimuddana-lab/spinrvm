"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    locationTrail?: { lat: number; lng: number; timestamp?: string }[];
}

export default function RideRouteMap({ pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail }: Props) {
    const mapRef = useRef<HTMLDivElement>(null);
    const mapInstance = useRef<L.Map | null>(null);

    useEffect(() => {
        if (!mapRef.current || mapInstance.current) return;

        const map = L.map(mapRef.current, { zoomControl: false }).setView(
            [(pickupLat + dropoffLat) / 2, (pickupLng + dropoffLng) / 2],
            13
        );
        mapInstance.current = map;

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "",
        }).addTo(map);

        // Pickup marker (green)
        L.circleMarker([pickupLat, pickupLng], {
            radius: 8, fillColor: "#10b981", color: "#fff", weight: 2, fillOpacity: 1,
        }).addTo(map).bindPopup("Pickup");

        // Dropoff marker (red/blue)
        L.circleMarker([dropoffLat, dropoffLng], {
            radius: 8, fillColor: "#3b82f6", color: "#fff", weight: 2, fillOpacity: 1,
        }).addTo(map).bindPopup("Dropoff");

        // Planned route (dashed line)
        L.polyline([[pickupLat, pickupLng], [dropoffLat, dropoffLng]], {
            color: "#9ca3af", weight: 2, dashArray: "8 6", opacity: 0.6,
        }).addTo(map);

        // Actual route (solid blue polyline from GPS trail)
        if (locationTrail && locationTrail.length > 1) {
            const trailPoints: [number, number][] = locationTrail.map(p => [p.lat, p.lng]);
            L.polyline(trailPoints, {
                color: "#3b82f6", weight: 3, opacity: 0.8,
            }).addTo(map);
        }

        // Fit bounds
        const allPoints: [number, number][] = [
            [pickupLat, pickupLng],
            [dropoffLat, dropoffLng],
            ...(locationTrail || []).map(p => [p.lat, p.lng] as [number, number]),
        ];
        if (allPoints.length >= 2) {
            map.fitBounds(L.latLngBounds(allPoints), { padding: [30, 30] });
        }

        return () => {
            map.remove();
            mapInstance.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng, locationTrail]);

    return <div ref={mapRef} className="w-full h-[280px] rounded-xl overflow-hidden" />;
}
