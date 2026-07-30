// FAQs, legal documents (ToS/Privacy), area pricing/tax fees, and driver
// area assignment. Extracted from the monolithic lib/api.ts as part of
// the per-domain split.

import { request } from "./client";

/* ── FAQs ───────────────────────────────────── */
export const getFaqs = () =>
    request<any[]>("/api/admin/faqs");

export const createFaq = (data: any) =>
    request<any>("/api/admin/faqs", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateFaq = (id: string, data: any) =>
    request<any>(`/api/admin/faqs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteFaq = (id: string) =>
    request<any>(`/api/admin/faqs/${id}`, { method: "DELETE" });

/* ── Legal Documents (per-audience ToS / Privacy) ─────────────── */
export const getLegalDocuments = () =>
    request<any[]>("/api/admin/legal-documents");

export const upsertLegalDocument = (data: {
    audience: "rider" | "driver";
    type: "tos" | "privacy";
    content: string;
}) =>
    request<any>("/api/admin/legal-documents", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Notifications (uses sendNotification defined above) ── */

/* ── Area Management (Pricing, Tax, Vehicle Pricing) ─────────────────── */
export const getAreaFees = (areaId: string) =>
    request<any[]>(`/api/admin/areas/${areaId}/fees`);

export const createAreaFee = (areaId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/fees`, {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateAreaFee = (areaId: string, feeId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/fees/${feeId}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteAreaFee = (areaId: string, feeId: string) =>
    request<any>(`/api/admin/areas/${areaId}/fees/${feeId}`, { method: "DELETE" });

export const getAreaTax = (areaId: string) =>
    request<any>(`/api/admin/areas/${areaId}/tax`);

export const updateAreaTax = (areaId: string, data: any) =>
    request<any>(`/api/admin/areas/${areaId}/tax`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const getVehiclePricing = (areaId: string) =>
    request<any>(`/api/admin/areas/${areaId}/vehicle-pricing`);

/* ── Driver Area Assignment ──────────────────── */
export const assignDriverArea = (driverId: string, serviceAreaId: string) =>
    request<any>(`/api/admin/drivers/${driverId}/area?service_area_id=${serviceAreaId}`, {
        method: "PUT",
    });

export const driverAction = (driverId: string, action: string, reason?: string) =>
    request<{ message: string; new_status: string; audit_log_id?: string }>(`/api/admin/drivers/${driverId}/action`, {
        method: "POST",
        body: JSON.stringify({ action, reason }),
    });

export const getDriverVehicleHistory = (driverId: string) =>
    request<{ history: Array<{ id: string; field: string; old_value: string | null; new_value: string | null; changed_by_role: string; created_at: string }> }>(
        `/api/admin/drivers/${driverId}/vehicle-history`,
    );

export const reviewDriverPhoto = (driverId: string, action: "approve" | "reject") =>
    request<{ message: string; profile_image_status: string }>(`/api/admin/drivers/${driverId}/photo-review`, {
        method: "POST",
        body: JSON.stringify({ action }),
    });

/** Upload a driver's profile photo on their behalf (stored approved). */
export const uploadDriverPhoto = (driverId: string, file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<{ message: string; profile_image: string; profile_image_status: string }>(
        `/api/admin/drivers/${driverId}/photo`,
        { method: "POST", body: fd },
    );
};

export const overrideDriverStatus = (driverId: string, status: string, reason?: string) =>
    request<any>(`/api/admin/drivers/${driverId}/status-override`, {
        method: "PUT",
        body: JSON.stringify({ status, reason }),
    });

export const exportDrivers = () =>
    request<{ drivers: any[]; count: number }>("/api/admin/export/drivers");

