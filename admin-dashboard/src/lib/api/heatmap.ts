// Heat map demand/supply data and per-area heat map settings. Extracted
// from the monolithic lib/api.ts as part of the per-domain split.

import { request } from "./client";

/* ── Heat Map Data ─────────────────────────── */
export interface HeatMapData {
    pickup_points: [number, number, number][];
    dropoff_points: [number, number, number][];
    stats: {
        total_rides: number;
        corporate_rides: number;
        regular_rides: number;
    };
}

export const getHeatMapData = (params: {
    filter?: string;
    start_date?: string;
    end_date?: string;
    service_area_id?: string;
    group_by?: string;
}) => {
    const searchParams = new URLSearchParams();
    if (params.filter) searchParams.set('filter', params.filter);
    if (params.start_date) searchParams.set('start_date', params.start_date);
    if (params.end_date) searchParams.set('end_date', params.end_date);
    if (params.service_area_id) searchParams.set('service_area_id', params.service_area_id);
    if (params.group_by) searchParams.set('group_by', params.group_by);

    return request<HeatMapData>(`/api/admin/rides/heatmap-data?${searchParams.toString()}`);
};

/* ── Heat Map Settings ─────────────────────── */
export interface HeatMapSettings {
    heat_map_enabled: boolean;
    heat_map_default_range: string;
    heat_map_intensity: string;
    heat_map_radius: number;
    heat_map_blur: number;
    heat_map_gradient_start: string;
    heat_map_gradient_mid: string;
    heat_map_gradient_end: string;
    heat_map_show_pickups: boolean;
    heat_map_show_dropoffs: boolean;
    corporate_heat_map_enabled: boolean;
    regular_rider_heat_map_enabled: boolean;
}

export const getHeatMapSettings = () =>
    request<HeatMapSettings>("/api/admin/settings/heatmap");

export const updateHeatMapSettings = (data: Partial<HeatMapSettings>) =>
    request<any>("/api/admin/settings/heatmap", {
        method: "PUT",
        body: JSON.stringify(data),
    });

