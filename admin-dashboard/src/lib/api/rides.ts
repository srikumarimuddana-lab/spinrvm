// Ride management, flags, complaints, and lost-and-found. Extracted from the
// monolithic lib/api.ts as part of the per-domain split — grouped by original
// file position, not a strict domain boundary (deleteDispute lives here
// alongside deleteComplaint; createDispute is in api/disputes-safety.ts).

import { request } from "./client";
import { useAuthStore } from "@/store/authStore";

/* ── Rides ────────────────────────────────── */
export interface RideListOpts {
    isScheduled?: boolean;
    status?: string;
    search?: string;
    dateFrom?: string;
    dateTo?: string;
    serviceAreaId?: string;
    sortBy?: string;
    sortDir?: "asc" | "desc";
}
export const getRides = (
    limit = 25,
    offset = 0,
    opts?: RideListOpts,
) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (opts?.isScheduled !== undefined) params.set("is_scheduled", String(opts.isScheduled));
    if (opts?.status) params.set("status", opts.status);
    if (opts?.search) params.set("search", opts.search);
    if (opts?.dateFrom) params.set("date_from", opts.dateFrom);
    if (opts?.dateTo) params.set("date_to", opts.dateTo);
    if (opts?.serviceAreaId) params.set("service_area_id", opts.serviceAreaId);
    if (opts?.sortBy) params.set("sort_by", opts.sortBy);
    if (opts?.sortDir) params.set("sort_dir", opts.sortDir);
    return request<{ rides: any[]; total_count: number; limit: number; offset: number }>(
        `/api/admin/rides?${params.toString()}`,
    );
};
export const exportRides = (opts?: RideListOpts) => {
    const params = new URLSearchParams();
    if (opts?.isScheduled !== undefined) params.set("is_scheduled", String(opts.isScheduled));
    if (opts?.status) params.set("status", opts.status);
    if (opts?.search) params.set("search", opts.search);
    if (opts?.dateFrom) params.set("date_from", opts.dateFrom);
    if (opts?.dateTo) params.set("date_to", opts.dateTo);
    if (opts?.serviceAreaId) params.set("service_area_id", opts.serviceAreaId);
    if (opts?.sortBy) params.set("sort_by", opts.sortBy);
    if (opts?.sortDir) params.set("sort_dir", opts.sortDir);
    return request<{ rides: any[]; total_count: number }>(
        `/api/admin/rides/export?${params.toString()}`,
    );
};
export const getRideDetails = (id: string) =>
    request<any>(`/api/admin/rides/${id}/details`);
export const getRideTrend = (days = 14) =>
    request<{ daily_chart: { date: string; date_iso: string; rides: number }[]; days: number }>(
        `/api/admin/rides/trend?days=${days}`,
    );
export const getRideStats = () =>
    request<{
        today_count: number;
        yesterday_count: number;
        this_week_count: number;
        this_month_count: number;
        week_start: string;
        week_end: string;
        month_start: string;
        month_end: string;
    }>("/api/admin/rides/stats");
export type RideFinancialsPeriod = "today" | "yesterday" | "week" | "month";
export const getRideFinancials = (period: RideFinancialsPeriod = "today") =>
    request<{
        period: RideFinancialsPeriod;
        label: string;
        rides_count: number;
        completed_count: number;
        rider_paid: number;
        gross_fare: number;
        driver_revenue: number;
        driver_take: number;
        tips: number;
        incentives: number;
        gst_collected: number;
        promo_applied: number;
        area_fees: number;
        platform_before_promo: number;
        platform_after_promo: number;
    }>(`/api/admin/rides/financials?period=${period}`);
export const getRideLocationTrail = (rideId: string) =>
    request<any[]>(`/api/admin/rides/${rideId}/location-trail`);
export const getLiveRideData = (rideId: string) =>
    request<any>(`/api/admin/rides/${rideId}/live`);
export const getRideInvoice = (rideId: string) =>
    request<any>(`/api/admin/rides/${rideId}/invoice`);

/** Fetch the ride's route map PNG via the backend proxy. Returns a data URL
 *  (base64) or null on failure. Never exposes the Google Maps API key. */
export const getRideRouteMapDataUrl = async (rideId: string): Promise<string | null> => {
    const token = useAuthStore.getState().token;
    try {
        const res = await fetch(`/api/admin/rides/${rideId}/route-map.png`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return null;
        const blob = await res.blob();
        return await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onloadend = () => resolve(reader.result as string);
            reader.onerror = reject;
            reader.readAsDataURL(blob);
        });
    } catch (e) {
        if (process.env.NODE_ENV === "development") {
            console.log("Failed to fetch ride route map:", e);
        }
        return null;
    }
};
export const flagRideParticipant = (rideId: string, data: { target_type: string; reason: string; description?: string; service_area_id?: string | null }) =>
    request<any>(`/api/admin/rides/${rideId}/flag`, {
        method: "POST",
        body: JSON.stringify(data),
    });
export const createRideComplaint = (rideId: string, data: { against_type: string; category: string; description: string; service_area_id?: string | null }) =>
    request<any>(`/api/admin/rides/${rideId}/complaint`, {
        method: "POST",
        body: JSON.stringify(data),
    });
export const resolveComplaint = (complaintId: string, data: { status: string; resolution: string }) =>
    request<any>(`/api/admin/complaints/${complaintId}/resolve`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const reportLostItem = (rideId: string, data: { item_description: string; service_area_id?: string | null }) =>
    request<any>(`/api/admin/rides/${rideId}/lost-and-found`, {
        method: "POST",
        body: JSON.stringify(data),
    });
export const resolveLostItem = (itemId: string, data: { status: string; admin_notes?: string }) =>
    request<any>(`/api/admin/lost-and-found/${itemId}/resolve`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const sendRideInvoice = (rideId: string, email?: string) =>
    request<any>(`/api/admin/rides/${rideId}/send-receipt`, {
        method: "POST",
        // Optional override address — when omitted the backend emails the
        // rider on file.
        body: email ? JSON.stringify({ email }) : undefined,
    });
// Payable Stripe Invoice for a stuck unpaid ride — Stripe emails the rider a
// hosted pay page; invoice.paid settles the ride. Returns { invoice_url }.
export const sendPayableRideInvoice = (rideId: string) =>
    request<{ invoice_url?: string; stripe_invoice_id?: string }>(
        `/api/admin/rides/${rideId}/send-invoice`,
        { method: "POST" },
    );
export const getFlags = (opts: {
    limit?: number;
    offset?: number;
    target_type?: string;
    service_area_id?: string;
    is_active?: boolean;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.target_type) sp.set("target_type", opts.target_type);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    if (opts.is_active != null) sp.set("is_active", String(opts.is_active));
    const qs = sp.toString();
    return request<any[]>(`/api/admin/flags${qs ? `?${qs}` : ""}`);
};
export const deactivateFlag = (flagId: string) =>
    request<any>(`/api/admin/flags/${flagId}/deactivate`, { method: "PUT" });
export const deleteFlag = (flagId: string) =>
    request<any>(`/api/admin/flags/${flagId}`, { method: "DELETE" });
export const getLostAndFoundItems = (opts: {
    limit?: number;
    offset?: number;
    status?: string;
    service_area_id?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status) sp.set("status", opts.status);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/lost-and-found${qs ? `?${qs}` : ""}`);
};
export const updateLostItem = (itemId: string, data: any) =>
    request<any>(`/api/admin/lost-and-found/${itemId}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteLostItem = (itemId: string) =>
    request<any>(`/api/admin/lost-and-found/${itemId}`, { method: "DELETE" });
export const deleteDispute = (disputeId: string) =>
    request<any>(`/api/admin/disputes/${disputeId}`, { method: "DELETE" });
export const getComplaints = (opts: {
    limit?: number;
    offset?: number;
    status?: string;
    against_type?: string;
    service_area_id?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status) sp.set("status", opts.status);
    if (opts.against_type) sp.set("against_type", opts.against_type);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/complaints${qs ? `?${qs}` : ""}`);
};
export const deleteComplaint = (complaintId: string) =>
    request<any>(`/api/admin/complaints/${complaintId}`, { method: "DELETE" });

