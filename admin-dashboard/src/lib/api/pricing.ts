// Pricing/config surfaces: service areas, pickup venues, ride incentives,
// surge status, vehicle types, fare configs, and dual-approval export
// requests. Extracted from the monolithic lib/api.ts as part of the
// per-domain split.

import { request } from "./client";

/* ── Service Areas ────────────────────────── */
export const getServiceAreas = () =>
    request<any[]>("/api/admin/service-areas");

/* ── Pickup Venues ───────────────────────────── */
export interface VenuePickupPoint { name: string; lat: number; lng: number; }
export interface Venue {
    id: string;
    name: string;
    center_lat: number;
    center_lng: number;
    radius_m: number;
    pickup_points: VenuePickupPoint[];
    service_area_id?: string | null;
    is_active: boolean;
}
export type VenueUpsert = Omit<Venue, "id">;
export const getVenues = (params?: { service_area_id?: string }) => {
    const qs = params?.service_area_id ? `?service_area_id=${encodeURIComponent(params.service_area_id)}` : "";
    return request<{ venues: Venue[] }>(`/api/admin/venues${qs}`);
};
export const createVenue = (body: VenueUpsert) =>
    request<Venue>("/api/admin/venues", { method: "POST", body: JSON.stringify(body) });
export const updateVenue = (id: string, body: VenueUpsert) =>
    request<Venue>(`/api/admin/venues/${id}`, { method: "PUT", body: JSON.stringify(body) });
export const deleteVenue = (id: string) =>
    request<{ success: boolean }>(`/api/admin/venues/${id}`, { method: "DELETE" });
export const createServiceArea = (data: any) =>
    request<any>("/api/admin/service-areas", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateServiceArea = (id: string, data: any) =>
    request<any>(`/api/admin/service-areas/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteServiceArea = (id: string) =>
    request<any>(`/api/admin/service-areas/${id}`, { method: "DELETE" });

/* ── Ride Incentives ──────────────────────── */
export const getIncentives = (areaId?: string) =>
    request<any[]>(`/api/admin/incentives${areaId ? `?service_area_id=${areaId}` : ''}`);
export const createIncentive = (data: any) =>
    request<any>("/api/admin/incentives", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateIncentive = (id: string, data: any) =>
    request<any>(`/api/admin/incentives/${id}`, {
        method: "PATCH",
        body: JSON.stringify(data),
    });
export const toggleIncentive = (id: string) =>
    request<any>(`/api/admin/incentives/${id}/toggle`, { method: "PATCH" });
export const deleteIncentive = (id: string) =>
    request<any>(`/api/admin/incentives/${id}`, { method: "DELETE" });

/* ── Surge Pricing ────────────────────────── */
export const getSurgeStatus = () =>
    request<any[]>("/api/admin/surge/status");
export const resetSurgeToAuto = (id: string) =>
    request<any>(`/api/v1/service-areas/${id}/surge/auto`, { method: "PUT" });

/* ── Vehicle Types ────────────────────────── */
export const getVehicleTypes = () =>
    request<any[]>("/api/admin/vehicle-types");
export const createVehicleType = (data: any) =>
    request<any>("/api/admin/vehicle-types", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateVehicleType = (id: string, data: any) =>
    request<any>(`/api/admin/vehicle-types/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteVehicleType = (id: string) =>
    request<any>(`/api/admin/vehicle-types/${id}`, { method: "DELETE" });

/**
 * Upload a PNG/JPEG/WebP illustration for a vehicle type. ≤500 KB.
 * Returns the public URL stored on `vehicle_types.illustration_url`.
 */
export const adminUploadVehicleIllustration = (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ illustration_url: string }>(
        `/api/admin/vehicle-types/${id}/upload-illustration`,
        { method: "POST", body: fd },
    );
};

/**
 * Upload a custom map marker image for a vehicle type. Transparent
 * PNG/WebP only (car facing north), ≤500 KB. Returns the public URL
 * stored on `vehicle_types.marker_image_url`.
 */
export const adminUploadVehicleMarker = (id: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ marker_image_url: string }>(
        `/api/admin/vehicle-types/${id}/upload-marker`,
        { method: "POST", body: fd },
    );
};

/**
 * Upload the driver-app ride-offer alert tone. mp3/wav, ≤500 KB.
 * Returns the public URL stored on `settings.ride_offer_sound_url`.
 */
export const adminUploadRideOfferSound = (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ ride_offer_sound_url: string }>(
        "/api/admin/settings/ride-offer-sound",
        { method: "POST", body: fd },
    );
};

/* ── Fare Configs ─────────────────────────── */
export const getFareConfigs = () =>
    request<any[]>("/api/admin/fare-configs");
export const createFareConfig = (data: any) =>
    request<any>("/api/admin/fare-configs", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateFareConfig = (id: string, data: any) =>
    request<any>(`/api/admin/fare-configs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteFareConfig = (id: string) =>
    request<any>(`/api/admin/fare-configs/${id}`, { method: "DELETE" });

/* ── Surge Pricing ────────────────────────── */
export const updateSurge = (areaId: string, data: any) =>
    request<any>(`/api/admin/service-areas/${areaId}/surge`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Export Approvals (ACTION_ITEMS.md B10 dual-approval gate) ────────── */
export interface ExportApprovalRequest {
    id: string;
    requested_by: string;
    route_key: string;
    params: Record<string, any>;
    row_count: number | null;
    reason: string | null;
    status: "pending" | "approved" | "denied" | "expired" | "consumed";
    created_at: string;
}

export const getPendingExportApprovals = () =>
    request<ExportApprovalRequest[]>("/api/admin/export-approvals/pending");

export const approveExportRequest = (requestId: string, decisionNote = "") =>
    request<ExportApprovalRequest>(`/api/admin/export-approvals/${requestId}/approve`, {
        method: "POST",
        body: JSON.stringify({ decision_note: decisionNote }),
    });

export const denyExportRequest = (requestId: string, decisionNote = "") =>
    request<ExportApprovalRequest>(`/api/admin/export-approvals/${requestId}/deny`, {
        method: "POST",
        body: JSON.stringify({ decision_note: decisionNote }),
    });

