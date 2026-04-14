"use client";

import { useEffect, useRef } from "react";
import maplibregl from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_URL,
    addStandardControls,
    fitBoundsToPoints,
    makeCircleMarkerEl,
    polygonPointsToGeoJSON,
} from "@/lib/map/maplibre-base";

interface ServiceArea {
    id: string;
    name?: string;
    is_airport?: boolean;
    polygon?: { lat: number; lng: number }[];
}

interface Driver {
    id?: string;
    name?: string;
    phone?: string;
    vehicle_color?: string;
    vehicle_make?: string;
    vehicle_model?: string;
    license_plate?: string;
    rating?: number;
    total_rides?: number;
    is_online?: boolean;
    current_lat?: number;
    current_lng?: number;
}

interface DriverMapProps {
    drivers: Driver[];
    serviceAreas?: ServiceArea[];
    selectedArea?: string;
}

const AREAS_SOURCE_ID = "driver-map-areas";
const AREAS_FILL_LAYER_ID = "driver-map-areas-fill";
const AREAS_LINE_LAYER_ID = "driver-map-areas-line";

export default function DriverMap({ drivers, serviceAreas = [], selectedArea = "all" }: DriverMapProps) {
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const markersRef = useRef<maplibregl.Marker[]>([]);
    const popupsRef = useRef<maplibregl.Popup[]>([]);
    const isLoadedRef = useRef(false);

    // Initialise map once
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_URL,
            center: [-79.4, 43.7], // Toronto
            zoom: 11,
        });
        addStandardControls(map);
        mapRef.current = map;

        map.on("load", () => {
            isLoadedRef.current = true;
            // Empty feature collection for service-area polygons; filled
            // on subsequent effect runs
            map.addSource(AREAS_SOURCE_ID, {
                type: "geojson",
                data: { type: "FeatureCollection", features: [] },
            });
            map.addLayer({
                id: AREAS_FILL_LAYER_ID,
                type: "fill",
                source: AREAS_SOURCE_ID,
                paint: {
                    "fill-color": ["case",
                        ["boolean", ["get", "is_airport"], false], "#0ea5e9",
                        "#8b5cf6",
                    ],
                    "fill-opacity": 0.08,
                },
            });
            map.addLayer({
                id: AREAS_LINE_LAYER_ID,
                type: "line",
                source: AREAS_SOURCE_ID,
                paint: {
                    "line-color": ["case",
                        ["boolean", ["get", "is_airport"], false], "#0ea5e9",
                        "#8b5cf6",
                    ],
                    "line-width": 2,
                    "line-dasharray": ["case",
                        ["boolean", ["get", "is_airport"], false], ["literal", [3, 2]],
                        ["literal", [1]],
                    ],
                },
            });
        });

        return () => {
            markersRef.current.forEach((m) => m.remove());
            markersRef.current = [];
            popupsRef.current.forEach((p) => p.remove());
            popupsRef.current = [];
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
    }, []);

    // Update driver markers + service-area polygons when data changes
    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;

        const apply = () => {
            // ── Service-area polygons ────────────────────────────────
            const features: GeoJSON.Feature[] = [];
            serviceAreas.forEach((area) => {
                const geo = polygonPointsToGeoJSON(area.polygon ?? []);
                if (!geo) return;
                features.push({
                    type: "Feature",
                    properties: {
                        id: area.id,
                        name: area.name || "Service Area",
                        is_airport: !!area.is_airport,
                    },
                    geometry: geo,
                });
            });
            const src = map.getSource(AREAS_SOURCE_ID) as maplibregl.GeoJSONSource | undefined;
            if (src) {
                src.setData({ type: "FeatureCollection", features });
            }

            // ── Driver markers ───────────────────────────────────────
            markersRef.current.forEach((m) => m.remove());
            markersRef.current = [];
            popupsRef.current.forEach((p) => p.remove());
            popupsRef.current = [];

            const withLocation = drivers.filter((d) => d.current_lat && d.current_lng);
            withLocation.forEach((driver) => {
                const color = driver.is_online ? "#10b981" : "#71717a";
                const el = makeCircleMarkerEl({
                    color,
                    title: driver.name ?? "",
                    size: 14,
                });

                const rating = driver.rating?.toFixed(1) || "5.0";
                const status = driver.is_online
                    ? '<span style="color:#10b981;font-weight:600">● Online</span>'
                    : '<span style="color:#71717a;font-weight:600">● Offline</span>';
                const popupHtml = `<div style="min-width:180px;font-family:system-ui;font-size:13px;line-height:1.5">
                    <strong style="font-size:14px">${driver.name || "Unknown"}</strong><br/>
                    ${status}<br/>
                    <span style="color:#888">📱</span> ${driver.phone || "—"}<br/>
                    <span style="color:#888">🚗</span> ${driver.vehicle_color || ""} ${driver.vehicle_make || ""} ${driver.vehicle_model || ""}<br/>
                    <span style="color:#888">🔢</span> ${driver.license_plate || "—"}<br/>
                    <span style="color:#f59e0b">★</span> ${rating}
                    <span style="margin-left:8px;color:#888">${driver.total_rides || 0} rides</span>
                </div>`;

                const popup = new maplibregl.Popup({ closeButton: false, offset: 8 }).setHTML(popupHtml);

                const marker = new maplibregl.Marker({ element: el })
                    .setLngLat([driver.current_lng!, driver.current_lat!])
                    .setPopup(popup)
                    .addTo(map);

                el.addEventListener("mouseenter", () => marker.togglePopup());
                el.addEventListener("mouseleave", () => {
                    if (popup.isOpen()) popup.remove();
                });

                markersRef.current.push(marker);
                popupsRef.current.push(popup);
            });

            // Auto-fit to drivers when we have markers
            if (withLocation.length > 0) {
                fitBoundsToPoints(
                    map,
                    withLocation.map((d) => ({ lat: d.current_lat!, lng: d.current_lng! })),
                    60,
                );
            }
        };

        if (isLoadedRef.current) apply();
        else map.once("load", apply);
    }, [drivers, serviceAreas]);

    // Pan to selected area
    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;
        if (selectedArea === "all") {
            const withLocation = drivers.filter((d) => d.current_lat && d.current_lng);
            if (withLocation.length > 0) {
                fitBoundsToPoints(
                    map,
                    withLocation.map((d) => ({ lat: d.current_lat!, lng: d.current_lng! })),
                    60,
                );
            }
            return;
        }
        const area = serviceAreas.find((a) => a.id === selectedArea);
        const geo = polygonPointsToGeoJSON(area?.polygon ?? []);
        if (!geo) return;
        const ring = geo.coordinates[0];
        fitBoundsToPoints(map, ring.map((p) => [p[0], p[1]] as [number, number]), 60);
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
