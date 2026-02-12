"use client";

import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

interface DriverMapProps {
    drivers: any[];
    serviceAreas?: any[];
    selectedArea?: string;
}

// Custom colored circle marker
function createCircleIcon(color: string) {
    return L.divIcon({
        className: "",
        html: `<div style="
            width:14px;height:14px;border-radius:50%;
            background:${color};border:2px solid #fff;
            box-shadow:0 1px 4px rgba(0,0,0,0.3);
        "></div>`,
        iconSize: [14, 14],
        iconAnchor: [7, 7],
    });
}

const onlineIcon = createCircleIcon("#10b981");
const offlineIcon = createCircleIcon("#71717a");

export default function DriverMap({ drivers, serviceAreas = [], selectedArea = "all" }: DriverMapProps) {
    const mapRef = useRef<L.Map | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);

    useEffect(() => {
        if (!containerRef.current) return;

        if (mapRef.current) {
            mapRef.current.remove();
            mapRef.current = null;
        }

        const map = L.map(containerRef.current, {
            center: [43.7, -79.4],
            zoom: 11,
            zoomControl: true,
        });

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap",
        }).addTo(map);

        mapRef.current = map;

        // Draw service area polygons
        serviceAreas.forEach((area) => {
            const polygon = area.polygon;
            if (!polygon || polygon.length < 3) return;
            const latlngs = polygon.map((p: any) => [p.lat, p.lng] as [number, number]);
            const poly = L.polygon(latlngs, {
                color: area.is_airport ? "#0ea5e9" : "#8b5cf6",
                weight: 2,
                fillOpacity: 0.08,
                dashArray: area.is_airport ? "6 4" : undefined,
            }).addTo(map);
            poly.bindTooltip(area.name || "Service Area", {
                permanent: false,
                direction: "center",
                className: "font-medium",
            });
        });

        // Add driver markers
        const markers: L.Marker[] = [];
        const driversWithLocation = drivers.filter(
            (d) => d.current_lat && d.current_lng
        );

        driversWithLocation.forEach((driver) => {
            const icon = driver.is_online ? onlineIcon : offlineIcon;
            const marker = L.marker([driver.current_lat, driver.current_lng], { icon }).addTo(map);

            const rating = driver.rating?.toFixed(1) || "5.0";
            const status = driver.is_online
                ? '<span style="color:#10b981;font-weight:600">● Online</span>'
                : '<span style="color:#71717a;font-weight:600">● Offline</span>';

            marker.bindPopup(
                `<div style="min-width:180px;font-family:system-ui;font-size:13px;line-height:1.5">
                    <strong style="font-size:14px">${driver.name || "Unknown"}</strong><br/>
                    ${status}<br/>
                    <span style="color:#888">📱</span> ${driver.phone || "—"}<br/>
                    <span style="color:#888">🚗</span> ${driver.vehicle_color || ""} ${driver.vehicle_make || ""} ${driver.vehicle_model || ""}<br/>
                    <span style="color:#888">🔢</span> ${driver.license_plate || "—"}<br/>
                    <span style="color:#f59e0b">★</span> ${rating}
                    <span style="margin-left:8px;color:#888">${driver.total_rides || 0} rides</span>
                </div>`,
                { closeButton: false, offset: [0, -4] }
            );

            marker.on("mouseover", () => marker.openPopup());
            marker.on("mouseout", () => marker.closePopup());

            markers.push(marker);
        });

        // Auto-fit bounds
        if (markers.length > 0) {
            const group = L.featureGroup(markers);
            map.fitBounds(group.getBounds().pad(0.15));
        }

        return () => {
            map.remove();
            mapRef.current = null;
        };
    }, [drivers, serviceAreas]);

    // Pan to selected area when it changes
    useEffect(() => {
        if (!mapRef.current) return;
        if (selectedArea === "all") {
            const driversWithLocation = drivers.filter((d) => d.current_lat && d.current_lng);
            if (driversWithLocation.length > 0) {
                const bounds = L.latLngBounds(
                    driversWithLocation.map((d) => [d.current_lat, d.current_lng] as [number, number])
                );
                mapRef.current.fitBounds(bounds.pad(0.15));
            }
            return;
        }
        const area = serviceAreas.find((a) => a.id === selectedArea);
        if (!area?.polygon || area.polygon.length < 3) return;
        const bounds = L.latLngBounds(
            area.polygon.map((p: any) => [p.lat, p.lng] as [number, number])
        );
        mapRef.current.fitBounds(bounds.pad(0.1));
    }, [selectedArea, drivers, serviceAreas]);

    // Count stats
    const withLocation = drivers.filter((d) => d.current_lat && d.current_lng);
    const onlineWithLoc = withLocation.filter((d) => d.is_online);
    const offlineWithLoc = withLocation.filter((d) => !d.is_online);

    return (
        <div>
            {/* Legend bar */}
            <div className="flex items-center gap-6 px-4 py-3 bg-muted/50 border-b border-border text-sm flex-wrap">
                <span className="text-muted-foreground font-medium">
                    {withLocation.length} drivers on map
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="inline-block w-2.5 h-2.5 rounded-full bg-emerald-500" />
                    Online ({onlineWithLoc.length})
                </span>
                <span className="flex items-center gap-1.5">
                    <span className="inline-block w-2.5 h-2.5 rounded-full bg-zinc-400" />
                    Offline ({offlineWithLoc.length})
                </span>
                {drivers.length > withLocation.length && (
                    <span className="text-xs text-muted-foreground ml-auto">
                        {drivers.length - withLocation.length} without location
                    </span>
                )}
            </div>
            {/* Map */}
            <div ref={containerRef} style={{ height: "500px", width: "100%" }} />
        </div>
    );
}
