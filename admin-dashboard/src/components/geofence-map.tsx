"use client";

import { useEffect, useRef, useCallback } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import "leaflet-draw";
import "leaflet-draw/dist/leaflet.draw.css";

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

interface PolygonPoint {
    lat: number;
    lng: number;
}

interface GeofenceMapProps {
    polygon?: PolygonPoint[];
    center?: PolygonPoint;
    zoom?: number;
    onPolygonChange?: (polygon: PolygonPoint[]) => void;
    readonly?: boolean;
    height?: string;
}

export default function GeofenceMap({
    polygon,
    center,
    zoom = 12,
    onPolygonChange,
    readonly = false,
    height = "400px",
}: GeofenceMapProps) {
    const mapRef = useRef<L.Map | null>(null);
    const containerRef = useRef<HTMLDivElement>(null);
    const drawnItemsRef = useRef<L.FeatureGroup | null>(null);
    const drawControlRef = useRef<any>(null);

    const onPolygonChangeRef = useRef(onPolygonChange);
    useEffect(() => {
        onPolygonChangeRef.current = onPolygonChange;
    }, [onPolygonChange]);

    // Compute effective center
    const effectiveCenter = center
        ? center
        : polygon && polygon.length > 0
            ? {
                lat: polygon.reduce((s, p) => s + p.lat, 0) / polygon.length,
                lng: polygon.reduce((s, p) => s + p.lng, 0) / polygon.length,
            }
            : { lat: 52.13, lng: -106.67 }; // Default: Saskatoon

    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const map = L.map(containerRef.current, {
            center: [effectiveCenter.lat, effectiveCenter.lng],
            zoom,
        });

        L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap contributors",
            maxZoom: 19,
        }).addTo(map);

        const drawnItems = new L.FeatureGroup();
        map.addLayer(drawnItems);
        drawnItemsRef.current = drawnItems;

        // If we have an initial polygon, draw it
        if (polygon && polygon.length >= 3) {
            const latlngs = polygon.map((p) => [p.lat, p.lng] as L.LatLngTuple);
            const poly = L.polygon(latlngs, {
                color: "#7c3aed",
                fillColor: "#7c3aed",
                fillOpacity: 0.2,
                weight: 2,
            });
            drawnItems.addLayer(poly);
            map.fitBounds(poly.getBounds().pad(0.2));
        }

        if (!readonly) {
            const drawControl = new (L.Control as any).Draw({
                position: "topright",
                draw: {
                    polygon: {
                        allowIntersection: false,
                        shapeOptions: {
                            color: "#7c3aed",
                            fillColor: "#7c3aed",
                            fillOpacity: 0.2,
                            weight: 2,
                        },
                    },
                    rectangle: {
                        shapeOptions: {
                            color: "#7c3aed",
                            fillColor: "#7c3aed",
                            fillOpacity: 0.2,
                            weight: 2,
                        },
                    },
                    polyline: false,
                    circle: false,
                    circlemarker: false,
                    marker: false,
                },
                edit: {
                    featureGroup: drawnItems,
                    remove: true,
                },
            });
            map.addControl(drawControl);
            drawControlRef.current = drawControl;

            // Handle new polygon drawn
            map.on(L.Draw.Event.CREATED, (e: any) => {
                // Clear previous polygons (one polygon per service area)
                drawnItems.clearLayers();
                drawnItems.addLayer(e.layer);

                const latlngs = e.layer.getLatLngs()[0];
                const points: PolygonPoint[] = latlngs.map((ll: L.LatLng) => ({
                    lat: parseFloat(ll.lat.toFixed(6)),
                    lng: parseFloat(ll.lng.toFixed(6)),
                }));
                onPolygonChangeRef.current?.(points);
            });

            // Handle polygon edited
            map.on(L.Draw.Event.EDITED, (e: any) => {
                const layers = e.layers;
                layers.eachLayer((layer: any) => {
                    const latlngs = layer.getLatLngs()[0];
                    const points: PolygonPoint[] = latlngs.map((ll: L.LatLng) => ({
                        lat: parseFloat(ll.lat.toFixed(6)),
                        lng: parseFloat(ll.lng.toFixed(6)),
                    }));
                    onPolygonChangeRef.current?.(points);
                });
            });

            // Handle polygon deleted
            map.on(L.Draw.Event.DELETED, () => {
                onPolygonChangeRef.current?.([]);
            });
        }

        mapRef.current = map;

        // Resize fix for dialog mount timing
        setTimeout(() => map.invalidateSize(), 200);

        return () => {
            map.remove();
            mapRef.current = null;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Update polygon externally (e.g. preset selection)
    const updatePolygon = useCallback((newPolygon: PolygonPoint[]) => {
        if (!drawnItemsRef.current || !mapRef.current) return;
        drawnItemsRef.current.clearLayers();
        if (newPolygon.length >= 3) {
            const latlngs = newPolygon.map(
                (p) => [p.lat, p.lng] as L.LatLngTuple
            );
            const poly = L.polygon(latlngs, {
                color: "#7c3aed",
                fillColor: "#7c3aed",
                fillOpacity: 0.2,
                weight: 2,
            });
            drawnItemsRef.current.addLayer(poly);
            mapRef.current.fitBounds(poly.getBounds().pad(0.2));
        }
    }, []);

    // Expose updatePolygon via ref-like pattern
    useEffect(() => {
        if (polygon && mapRef.current && drawnItemsRef.current) {
            updatePolygon(polygon);
        }
    }, [polygon, updatePolygon]);

    return (
        <div
            ref={containerRef}
            style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }}
        />
    );
}
