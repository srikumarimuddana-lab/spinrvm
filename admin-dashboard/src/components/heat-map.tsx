"use client";

import { useEffect, useRef } from "react";
import maplibregl, { type ExpressionSpecification } from "maplibre-gl";
import "maplibre-gl/dist/maplibre-gl.css";
import {
    MAP_STYLE_POSITRON,
    addStandardControls,
    fitBoundsToPoints,
} from "@/lib/map/maplibre-base";

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

const PICKUP_SOURCE = "heat-pickup-src";
const DROPOFF_SOURCE = "heat-dropoff-src";
const PICKUP_LAYER = "heat-pickup-lyr";
const DROPOFF_LAYER = "heat-dropoff-lyr";

// Colour stops for heatmap-color expression. We mirror the previous
// Leaflet.heat gradients: blue-scale for pickups, green→red for dropoffs.
const PICKUP_GRADIENT_EXPR: ExpressionSpecification = [
    "interpolate", ["linear"], ["heatmap-density"],
    0.0, "rgba(0,0,0,0)",
    0.2, "#00ffff",
    0.4, "#00aaff",
    0.6, "#0066ff",
    0.8, "#0000ff",
    1.0, "#0000aa",
];

const DROPOFF_GRADIENT_EXPR: ExpressionSpecification = [
    "interpolate", ["linear"], ["heatmap-density"],
    0.0, "rgba(0,0,0,0)",
    0.2, "#00ff00",
    0.4, "#88ff00",
    0.6, "#ffff00",
    0.8, "#ff8800",
    1.0, "#ff0000",
];

function pointsToFeatureCollection(points: HeatMapPoint[]): GeoJSON.FeatureCollection {
    return {
        type: "FeatureCollection",
        features: points.map((p) => ({
            type: "Feature",
            properties: { intensity: p.intensity ?? 0.5 },
            geometry: { type: "Point", coordinates: [p.lng, p.lat] },
        })),
    };
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
    const containerRef = useRef<HTMLDivElement>(null);
    const mapRef = useRef<maplibregl.Map | null>(null);
    const isLoadedRef = useRef(false);

    // Init map once
    useEffect(() => {
        if (!containerRef.current || mapRef.current) return;

        const map = new maplibregl.Map({
            container: containerRef.current,
            style: MAP_STYLE_POSITRON, // grayscale so heat layers pop
            center: [center.lng, center.lat],
            zoom,
        });
        addStandardControls(map);
        mapRef.current = map;

        map.on("load", () => {
            isLoadedRef.current = true;

            map.addSource(PICKUP_SOURCE, { type: "geojson", data: pointsToFeatureCollection([]) });
            map.addSource(DROPOFF_SOURCE, { type: "geojson", data: pointsToFeatureCollection([]) });

            const radius = settings.radius ?? 25;
            const maxVal = settings.max ?? 1;

            map.addLayer({
                id: PICKUP_LAYER,
                type: "heatmap",
                source: PICKUP_SOURCE,
                paint: {
                    "heatmap-weight": [
                        "interpolate", ["linear"], ["get", "intensity"],
                        0, 0,
                        maxVal, 1,
                    ],
                    "heatmap-intensity": 1,
                    "heatmap-radius": radius,
                    "heatmap-color": PICKUP_GRADIENT_EXPR,
                    "heatmap-opacity": 0.8,
                },
            });
            map.addLayer({
                id: DROPOFF_LAYER,
                type: "heatmap",
                source: DROPOFF_SOURCE,
                paint: {
                    "heatmap-weight": [
                        "interpolate", ["linear"], ["get", "intensity"],
                        0, 0,
                        maxVal, 1,
                    ],
                    "heatmap-intensity": 1,
                    "heatmap-radius": radius,
                    "heatmap-color": DROPOFF_GRADIENT_EXPR,
                    "heatmap-opacity": 0.8,
                },
            });
        });

        // Resize fix for dialog / tab mount timing
        const resizeTimer = setTimeout(() => {
            mapRef.current?.resize();
        }, 200);

        return () => {
            clearTimeout(resizeTimer);
            map.remove();
            mapRef.current = null;
            isLoadedRef.current = false;
        };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    // Update data + visibility + radius when inputs change
    useEffect(() => {
        const map = mapRef.current;
        if (!map) return;

        const apply = () => {
            const pickupSrc = map.getSource(PICKUP_SOURCE) as maplibregl.GeoJSONSource | undefined;
            const dropoffSrc = map.getSource(DROPOFF_SOURCE) as maplibregl.GeoJSONSource | undefined;
            pickupSrc?.setData(pointsToFeatureCollection(showPickups ? pickupPoints : []));
            dropoffSrc?.setData(pointsToFeatureCollection(showDropoffs ? dropoffPoints : []));

            map.setLayoutProperty(PICKUP_LAYER, "visibility", showPickups ? "visible" : "none");
            map.setLayoutProperty(DROPOFF_LAYER, "visibility", showDropoffs ? "visible" : "none");

            const radius = settings.radius ?? 25;
            map.setPaintProperty(PICKUP_LAYER, "heatmap-radius", radius);
            map.setPaintProperty(DROPOFF_LAYER, "heatmap-radius", radius);

            const allPoints = [
                ...(showPickups ? pickupPoints : []),
                ...(showDropoffs ? dropoffPoints : []),
            ];
            if (allPoints.length > 0) {
                fitBoundsToPoints(map, allPoints, 50);
            }
        };
        if (isLoadedRef.current) apply();
        else map.once("load", apply);
    }, [pickupPoints, dropoffPoints, showPickups, showDropoffs, settings]);

    return (
        <div
            ref={containerRef}
            style={{ height, width: "100%", borderRadius: "8px", overflow: "hidden" }}
        />
    );
}
