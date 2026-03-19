// Use relative URL to go through Next.js proxy (avoids CORS and IPv6 issues)
// For production, set NEXT_PUBLIC_API_URL to your backend URL
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Import Zustand store for token management
import { useAuthStore } from "@/store/authStore";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    // Get token from Zustand store
    const token = useAuthStore.getState().token;
    const headers: Record<string, string> = {
        "Content-Type": "application/json",
        ...(options.headers as Record<string, string>),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;

    const url = `${API_BASE}${path}`;
    try {
        const res = await fetch(url, { ...options, headers });
        console.log(`API Request: ${options.method || 'GET'} ${path} -> ${res.status}`);

        if (res.status === 401) {
            // Clear auth state via Zustand
            useAuthStore.getState().logout();
            if (typeof window !== "undefined") {
                window.location.href = "/login";
            }
            throw new Error("Unauthorized");
        }

        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            console.error(`API Error: ${path}`, body);
            throw new Error(body.detail || body.message || res.statusText);
        }

        return res.json();
    } catch (err) {
        console.error(`API Request Failed: ${url}`, err);
        throw err;
    }
}

/* ── Auth ─────────────────────────────────── */
export interface AuthResponse {
    token: string;
    user: {
        id: string;
        phone: string;
        first_name?: string;
        last_name?: string;
        email?: string;
        role: string;
        profile_complete: boolean;
    };
    is_new_user: boolean;
}

export interface AdminLoginResponse {
    token: string;
    user: {
        id: string;
        email: string;
        role: string;
    };
}

export const loginAdmin = (phone: string, code: string) =>
    request<AuthResponse>("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
    });

export const loginAdminSession = (email: string, password: string) =>
    request<AdminLoginResponse>("/api/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
    });

export const sendOtp = (phone: string) =>
    request<{ success: boolean; dev_otp?: string }>("/api/auth/send-otp", {
        method: "POST",
        body: JSON.stringify({ phone }),
    });

/* ── Dashboard ────────────────────────────── */
export const getStats = () =>
    request<{
        total_rides: number;
        completed_rides: number;
        cancelled_rides: number;
        active_rides: number;
        total_drivers: number;
        online_drivers: number;
        total_users: number;
        total_driver_earnings: number;
        total_admin_earnings: number;
        total_tips: number;
    }>("/api/admin/stats");

/* ── Rides ────────────────────────────────── */
export const getRides = () => request<any[]>("/api/admin/rides");
export const getRideDetails = (id: string) =>
    request<any>(`/api/admin/rides/${id}/details`);

/* ── Drivers ──────────────────────────────── */
export const getDrivers = () => request<any[]>("/api/admin/drivers");
export const getDriverRides = (id: string) =>
    request<any>(`/api/admin/drivers/${id}/rides`);

/* ── Earnings ─────────────────────────────── */
export const getEarnings = () => request<any[]>("/api/admin/earnings");

/* ── Settings ─────────────────────────────── */
export const getSettings = () => request<any>("/api/admin/settings");
export const updateSettings = (data: any) =>
    request<any>("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Service Areas ────────────────────────── */
export const getServiceAreas = () =>
    request<any[]>("/api/admin/service-areas");
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

/* ── Document Requirements ───────────────── */
export const getRequirements = () =>
    request<any[]>("/api/admin/documents/requirements");

export const createRequirement = (data: any) =>
    request<any>("/api/admin/documents/requirements", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateRequirement = (id: string, data: any) =>
    request<any>(`/api/admin/documents/requirements/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteRequirement = (id: string) =>
    request<any>(`/api/admin/documents/requirements/${id}`, { method: "DELETE" });

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/admin/documents/drivers/${driverId}`);

export const reviewDocument = (docId: string, status: string, reason?: string) =>
    request<any>(`/api/admin/documents/${docId}/review`, {
        method: "POST",
        body: JSON.stringify({ status, rejection_reason: reason }),
    });

/* ── Corporate Accounts ─────────────────────── */
export const getCorporateAccounts = () =>
    request<any[]>("/api/admin/corporate-accounts");

export const createCorporateAccount = (data: any) =>
    request<any>("/api/admin/corporate-accounts", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateCorporateAccount = (id: string, data: any) =>
    request<any>(`/api/admin/corporate-accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteCorporateAccount = (id: string) =>
    request<any>(`/api/admin/corporate-accounts/${id}`, { method: "DELETE" });

/* ── Users (Riders) ─────────────────────────── */
export const getUsers = () =>
    request<any[]>("/api/admin/users");

export const getUserDetails = (id: string) =>
    request<any>(`/api/admin/users/${id}`);

export const updateUserStatus = (id: string, statusData: any) =>
    request<any>(`/api/admin/users/${id}/status`, {
        method: "PUT",
        body: JSON.stringify(statusData),
    });

/* ── Promotions ─────────────────────────────── */
export const getPromotions = () =>
    request<any[]>("/api/admin/promotions");

export const createPromotion = (data: any) =>
    request<any>("/api/admin/promotions", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updatePromotion = (id: string, data: any) =>
    request<any>(`/api/admin/promotions/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deletePromotion = (id: string) =>
    request<any>(`/api/admin/promotions/${id}`, { method: "DELETE" });

/* ── Disputes ───────────────────────────────── */
export const getDisputes = () =>
    request<any[]>("/api/admin/disputes");

export const getDisputeDetails = (id: string) =>
    request<any>(`/api/admin/disputes/${id}`);

export const resolveDispute = (id: string, resolution: any) =>
    request<any>(`/api/admin/disputes/${id}/resolve`, {
        method: "PUT",
        body: JSON.stringify(resolution),
    });

/* ── Support Tickets ────────────────────────── */
export const getTickets = () =>
    request<any[]>("/api/admin/tickets");

export const getTicketDetails = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`);

export const replyToTicket = (id: string, message: string) =>
    request<any>(`/api/admin/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ message }),
    });

export const closeTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}/close`, { method: "POST" });

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

/* ── Notifications ──────────────────────────── */
export const sendNotification = (data: { user_id: string; title: string; body: string }) =>
    request<any>("/api/admin/notifications/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

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
