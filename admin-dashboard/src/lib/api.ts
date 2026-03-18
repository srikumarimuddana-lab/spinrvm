// Use relative URL to go through Next.js proxy (avoids CORS and IPv6 issues)
// For production, set NEXT_PUBLIC_API_URL to your backend URL
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    const token =
        typeof window !== "undefined" ? localStorage.getItem("admin_token") : null;
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
            if (typeof window !== "undefined") {
                localStorage.removeItem("admin_token");
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

export const loginAdmin = (phone: string, code: string) =>
    request<AuthResponse>("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
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
    }>("/api/v1/admin/stats");

/* ── Rides ────────────────────────────────── */
export const getRides = () => request<any[]>("/api/v1/admin/rides");
export const getRideDetails = (id: string) =>
    request<any>(`/api/v1/admin/rides/${id}/details`);

/* ── Drivers ──────────────────────────────── */
export const getDrivers = () => request<any[]>("/api/v1/admin/drivers");
export const getDriverRides = (id: string) =>
    request<any>(`/api/v1/admin/drivers/${id}/rides`);

/* ── Earnings ─────────────────────────────── */
export const getEarnings = () => request<any[]>("/api/v1/admin/earnings");

/* ── Settings ─────────────────────────────── */
export const getSettings = () => request<any>("/api/v1/admin/settings");
export const updateSettings = (data: any) =>
    request<any>("/api/v1/admin/settings", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Service Areas ────────────────────────── */
export const getServiceAreas = () =>
    request<any[]>("/api/v1/admin/service-areas");
export const createServiceArea = (data: any) =>
    request<any>("/api/v1/admin/service-areas", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateServiceArea = (id: string, data: any) =>
    request<any>(`/api/v1/admin/service-areas/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteServiceArea = (id: string) =>
    request<any>(`/api/v1/admin/service-areas/${id}`, { method: "DELETE" });

/* ── Vehicle Types ────────────────────────── */
export const getVehicleTypes = () =>
    request<any[]>("/api/v1/admin/vehicle-types");
export const createVehicleType = (data: any) =>
    request<any>("/api/v1/admin/vehicle-types", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateVehicleType = (id: string, data: any) =>
    request<any>(`/api/v1/admin/vehicle-types/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteVehicleType = (id: string) =>
    request<any>(`/api/v1/admin/vehicle-types/${id}`, { method: "DELETE" });

/* ── Fare Configs ─────────────────────────── */
export const getFareConfigs = () =>
    request<any[]>("/api/v1/admin/fare-configs");
export const createFareConfig = (data: any) =>
    request<any>("/api/v1/admin/fare-configs", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateFareConfig = (id: string, data: any) =>
    request<any>(`/api/v1/admin/fare-configs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteFareConfig = (id: string) =>
    request<any>(`/api/v1/admin/fare-configs/${id}`, { method: "DELETE" });

/* ── Support (Tickets & FAQs) ─────────────── */
export const getTickets = () => request<any[]>("/api/v1/admin/tickets");
export const getTicketDetails = (id: string) =>
    request<any>(`/api/v1/admin/tickets/${id}`);
export const replyToTicket = (id: string, message: string) =>
    request<any>(`/api/v1/admin/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ message }),
    });
export const closeTicket = (id: string) =>
    request<any>(`/api/v1/admin/tickets/${id}/close`, { method: "POST" });

export const getFaqs = () => request<any[]>("/api/v1/admin/faqs");
export const createFaq = (data: any) =>
    request<any>("/api/v1/admin/faqs", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateFaq = (id: string, data: any) =>
    request<any>(`/api/v1/admin/faqs/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteFaq = (id: string) =>
    request<any>(`/api/v1/admin/faqs/${id}`, { method: "DELETE" });

/* ── Surge Pricing ────────────────────────── */
export const updateSurge = (areaId: string, data: any) =>
    request<any>(`/api/v1/admin/service-areas/${areaId}/surge`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Notifications ────────────────────────── */
export const sendNotification = (data: { user_id: string; title: string; body: string }) =>
    request<any>("/api/v1/admin/notifications/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

/* ── Area Fees (Pricing) ─────────────────── */
export const getAreaFees = (areaId: string) =>
    request<any[]>(`/api/v1/admin/areas/${areaId}/fees`);
export const createAreaFee = (areaId: string, data: any) =>
    request<any>(`/api/v1/admin/areas/${areaId}/fees`, {
        method: "POST",
        body: JSON.stringify(data),
    });
export const updateAreaFee = (areaId: string, feeId: string, data: any) =>
    request<any>(`/api/v1/admin/areas/${areaId}/fees/${feeId}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });
export const deleteAreaFee = (areaId: string, feeId: string) =>
    request<any>(`/api/v1/admin/areas/${areaId}/fees/${feeId}`, { method: "DELETE" });

/* ── Tax Config ──────────────────────────── */
export const getAreaTax = (areaId: string) =>
    request<any>(`/api/v1/admin/areas/${areaId}/tax`);
export const updateAreaTax = (areaId: string, data: any) =>
    request<any>(`/api/v1/admin/areas/${areaId}/tax`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── Vehicle Pricing per Area ────────────── */
export const getVehiclePricing = (areaId: string) =>
    request<any>(`/api/v1/admin/areas/${areaId}/vehicle-pricing`);

/* ── Driver Area Assignment ──────────────── */
export const assignDriverArea = (driverId: string, serviceAreaId: string) =>
    request<any>(`/api/v1/admin/drivers/${driverId}/area?service_area_id=${serviceAreaId}`, {
        method: "PUT",
    });

/* ── Document Requirements ───────────────── */
export const getRequirements = () =>
    request<any[]>("/api/v1/admin/documents/requirements");

export const createRequirement = (data: any) =>
    request<any>("/api/v1/admin/documents/requirements", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateRequirement = (id: string, data: any) =>
    request<any>(`/api/v1/admin/documents/requirements/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteRequirement = (id: string) =>
    request<any>(`/api/v1/admin/documents/requirements/${id}`, { method: "DELETE" });

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/v1/admin/documents/drivers/${driverId}`);

export const reviewDocument = (docId: string, status: string, reason?: string) =>
    request<any>(`/api/v1/admin/documents/${docId}/review`, {
        method: "POST",
        body: JSON.stringify({ status, rejection_reason: reason }),
    });

/* ── Corporate Accounts ─────────────────────── */
export const getCorporateAccounts = () =>
    request<any[]>("/api/v1/admin/corporate-accounts");

export const createCorporateAccount = (data: any) =>
    request<any>("/api/v1/admin/corporate-accounts", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateCorporateAccount = (id: string, data: any) =>
    request<any>(`/api/v1/admin/corporate-accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteCorporateAccount = (id: string) =>
    request<any>(`/api/v1/admin/corporate-accounts/${id}`, { method: "DELETE" });

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

    return request<HeatMapData>(`/api/v1/admin/rides/heatmap-data?${searchParams.toString()}`);
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
    request<HeatMapSettings>("/api/v1/admin/settings/heatmap");

export const updateHeatMapSettings = (data: Partial<HeatMapSettings>) =>
    request<any>("/api/v1/admin/settings/heatmap", {
        method: "PUT",
        body: JSON.stringify(data),
    });
