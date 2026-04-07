"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface Props {
    pickupLat: number;
    pickupLng: number;
    dropoffLat: number;
    dropoffLng: number;
    driverLat?: number;
    driverLng?: number;
    trail?: { lat: number; lng: number }[];
}

export default function LiveRideMap({ pickupLat, pickupLng, dropoffLat, dropoffLng, driverLat, driverLng, trail }: Props) {
    const mapRef = useRef<HTMLDivElement>(null);
    const mapInstance = useRef<L.Map | null>(null);
    const driverMarker = useRef<L.CircleMarker | null>(null);
    const trailLine = useRef<L.Polyline | null>(null);

    // Initialize map once
    useEffect(() => {
        if (!mapRef.current || mapInstance.current) return;

        const map = L.map(mapRef.current, { zoomControl: true }).setView(
            [(pickupLat + dropoffLat) / 2, (pickupLng + dropoffLng) / 2],
            13
        );
        mapInstance.current = map;

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "",
        }).addTo(map);

        // Pickup marker (green)
        L.circleMarker([pickupLat, pickupLng], {
            radius: 10, fillColor: "#10b981", color: "#fff", weight: 3, fillOpacity: 1,
        }).addTo(map).bindPopup("Pickup");

        // Dropoff marker (blue)
        L.circleMarker([dropoffLat, dropoffLng], {
            radius: 10, fillColor: "#3b82f6", color: "#fff", weight: 3, fillOpacity: 1,
        }).addTo(map).bindPopup("Dropoff");

        // Planned route
        L.polyline([[pickupLat, pickupLng], [dropoffLat, dropoffLng]], {
            color: "#9ca3af", weight: 2, dashArray: "8 6", opacity: 0.6,
        }).addTo(map);

        // Driver marker (will be updated)
        driverMarker.current = L.circleMarker([0, 0], {
            radius: 8, fillColor: "#f59e0b", color: "#fff", weight: 3, fillOpacity: 1,
        }).addTo(map).bindPopup("Driver");

        // Trail polyline
        trailLine.current = L.polyline([], {
            color: "#3b82f6", weight: 3, opacity: 0.7,
        }).addTo(map);

        const bounds = L.latLngBounds([[pickupLat, pickupLng], [dropoffLat, dropoffLng]]);
        map.fitBounds(bounds, { padding: [50, 50] });

        return () => {
            map.remove();
            mapInstance.current = null;
        };
    }, [pickupLat, pickupLng, dropoffLat, dropoffLng]);

    // Update driver position and trail
    useEffect(() => {
        if (!mapInstance.current) return;

        if (driverLat && driverLng && driverMarker.current) {
            driverMarker.current.setLatLng([driverLat, driverLng]);
        }

        if (trail && trail.length > 0 && trailLine.current) {
            trailLine.current.setLatLngs(trail.map(p => [p.lat, p.lng]));
        }
    }, [driverLat, driverLng, trail]);

    return <div ref={mapRef} className="w-full h-full min-h-[400px]" />;
}
