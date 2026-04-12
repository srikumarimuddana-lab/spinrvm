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
            // Backend uses two error shapes:
            //   • FastAPI HTTPException  → { detail: "..." }
            //   • Custom error handler  → { error: { detail: "...", message: "..." } }
            const msg =
                body.detail ||
                body.error?.detail ||
                body.error?.message ||
                body.message ||
                res.statusText;
            throw new Error(msg);
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
export const getRides = (limit = 50, offset = 0) =>
    request<{ rides: any[]; total_count: number; limit: number; offset: number }>(
        `/api/admin/rides?limit=${limit}&offset=${offset}`
    );
export const getRideDetails = (id: string) =>
    request<any>(`/api/admin/rides/${id}/details`);
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
        const res = await fetch(`${API_BASE}/api/admin/rides/${rideId}/route-map.png`, {
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
        console.log("Failed to fetch ride route map:", e);
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
export const sendRideInvoice = async (rideId: string) => {
    const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const token = useAuthStore.getState().token;
    const res = await fetch(`${API_BASE}/api/v1/rides/${rideId}/process-payment`, {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ tip_amount: 0 }),
    });
    if (!res.ok) throw new Error("Failed to send invoice");
    return res.json();
};
export const getFlags = () => request<any[]>("/api/admin/flags");
export const deactivateFlag = (flagId: string) =>
    request<any>(`/api/admin/flags/${flagId}/deactivate`, { method: "PUT" });
export const deleteFlag = (flagId: string) =>
    request<any>(`/api/admin/flags/${flagId}`, { method: "DELETE" });
export const getLostAndFoundItems = () => request<any[]>("/api/admin/lost-and-found");
export const updateLostItem = (itemId: string, data: any) =>
    request<any>(`/api/admin/lost-and-found/${itemId}`, { method: "PUT", body: JSON.stringify(data) });
export const deleteLostItem = (itemId: string) =>
    request<any>(`/api/admin/lost-and-found/${itemId}`, { method: "DELETE" });
export const deleteDispute = (disputeId: string) =>
    request<any>(`/api/admin/disputes/${disputeId}`, { method: "DELETE" });
export const getComplaints = () => request<any[]>("/api/admin/complaints");
export const deleteComplaint = (complaintId: string) =>
    request<any>(`/api/admin/complaints/${complaintId}`, { method: "DELETE" });

/* ── Drivers ──────────────────────────────── */
export const getDrivers = () => request<any[]>("/api/admin/drivers");
export const getDriverRides = (id: string) =>
    request<any>(`/api/admin/drivers/${id}/rides`);

export const getDriverStats = (params?: {
    service_area_id?: string;
    start_date?: string;
    end_date?: string;
}) => {
    const sp = new URLSearchParams();
    if (params?.service_area_id) sp.set("service_area_id", params.service_area_id);
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    return request<{
        stats: {
            total: number;
            online: number;
            verified: number;
            unverified: number;
            total_rides: number;
            total_earnings: number;
            avg_rating: number;
        };
        area_stats: {
            service_area_id: string;
            service_area_name: string;
            total: number;
            online: number;
            verified: number;
            unverified: number;
            total_rides: number;
            total_earnings: number;
        }[];
        charts: {
            daily_joins: { date: string; date_raw: string; count: number }[];
            daily_rides: { date: string; date_raw: string; count: number }[];
            daily_earnings: { date: string; date_raw: string; amount: number }[];
        };
        drivers: any[];
        service_areas: { id: string; name: string }[];
    }>(`/api/admin/drivers/stats?${sp.toString()}`);
};

export const updateDriver = (id: string, data: Record<string, any>) =>
    request<any>(`/api/admin/drivers/${id}`, { method: "PUT", body: JSON.stringify(data) });

/* ── Earnings ─────────────────────────────── */
export const getEarnings = () => request<any[]>("/api/admin/earnings");

export const getSubscriptionStats = (params?: { start_date?: string; end_date?: string; service_area_ids?: string }) => {
    const sp = new URLSearchParams();
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    if (params?.service_area_ids) sp.set("service_area_ids", params.service_area_ids);
    return request<any>(`/api/admin/subscription-stats?${sp.toString()}`);
};

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

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/admin/documents/drivers/${driverId}`);

export const reviewDocument = (
    docId: string,
    status: string,
    reason?: string,
    expiryDate?: string,
) =>
    request<any>(`/api/admin/documents/${docId}/review`, {
        method: "POST",
        body: JSON.stringify({
            status,
            rejection_reason: reason,
            expiry_date: expiryDate,
        }),
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

/* ── Cloud Messaging (merged with Notifications) ── */
export const getCloudMessages = (status?: string, audience?: string) => {
    const params = new URLSearchParams();
    if (status) params.set('status', status);
    if (audience) params.set('audience', audience);
    return request<any[]>(`/api/admin/cloud-messaging?${params.toString()}`);
};

export const sendCloudMessage = (data: {
    title: string;
    description: string;
    audience: string;
    channels: string[];
    type?: string;
    particular_ids?: string[];
    scheduled_at?: string;
}) =>
    request<any>("/api/admin/cloud-messaging/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const getCloudMessageStats = () =>
    request<any>("/api/admin/cloud-messaging/stats");

export const deleteCloudMessage = (id: string) =>
    request<any>(`/api/admin/cloud-messaging/${id}`, { method: "DELETE" });

/* ── Promotions Usage & Stats ──────────────────── */
export const getPromoUsage = (params?: { promo_id?: string; date_from?: string; date_to?: string; limit?: number; offset?: number }) => {
    const sp = new URLSearchParams();
    if (params?.promo_id) sp.set('promo_id', params.promo_id);
    if (params?.date_from) sp.set('date_from', params.date_from);
    if (params?.date_to) sp.set('date_to', params.date_to);
    if (params?.limit) sp.set('limit', params.limit.toString());
    if (params?.offset) sp.set('offset', params.offset.toString());
    return request<any[]>(`/api/admin/promotions/usage?${sp.toString()}`);
};

export const getPromoStats = (range?: string) => {
    const sp = new URLSearchParams();
    if (range) sp.set('range', range);
    return request<any>(`/api/admin/promotions/stats?${sp.toString()}`);
};

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

export const createDispute = (data: any) =>
    request<any>("/api/admin/disputes", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateDispute = (id: string, data: any) =>
    request<any>(`/api/admin/disputes/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

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

export const createTicket = (data: any) =>
    request<any>("/api/admin/tickets", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateTicket = (id: string, data: any) =>
    request<any>(`/api/admin/tickets/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const replyToTicket = (id: string, message: string) =>
    request<any>(`/api/admin/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify({ message }),
    });

export const closeTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}/close`, { method: "POST" });

export const deleteTicket = (id: string) =>
    request<any>(`/api/admin/tickets/${id}`, { method: "DELETE" });

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
    request<{ message: string; new_status: string }>(`/api/admin/drivers/${driverId}/action`, {
        method: "POST",
        body: JSON.stringify({ action, reason }),
    });

export const overrideDriverStatus = (driverId: string, status: string, reason?: string) =>
    request<any>(`/api/admin/drivers/${driverId}/status-override`, {
        method: "PUT",
        body: JSON.stringify({ status, reason }),
    });

export const getDriverNotes = (driverId: string) =>
    request<any[]>(`/api/admin/drivers/${driverId}/notes`);

export const addDriverNote = (driverId: string, note: string, category: string = "general") =>
    request<any>(`/api/admin/drivers/${driverId}/notes`, {
        method: "POST",
        body: JSON.stringify({ note, category }),
    });

export const deleteDriverNote = (noteId: string) =>
    request<any>(`/api/admin/drivers/notes/${noteId}`, { method: "DELETE" });

export const getDriverActivity = (driverId: string) =>
    request<any[]>(`/api/admin/drivers/${driverId}/activity`);


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

/* ── Staff Management ──────────────────────── */
export const getStaff = () =>
    request<any[]>("/api/admin/staff");

export const createStaff = (data: { email: string; password: string; first_name: string; last_name: string; role: string; modules?: string[] }) =>
    request<any>("/api/admin/staff", { method: "POST", body: JSON.stringify(data) });

export const updateStaff = (id: string, data: any) =>
    request<any>(`/api/admin/staff/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteStaff = (id: string) =>
    request<any>(`/api/admin/staff/${id}`, { method: "DELETE" });

export const getStaffModules = () =>
    request<{ modules: string[]; role_presets: Record<string, string[]> }>("/api/admin/staff/modules/list");

/* ── Spinr Pass — Subscription Plans ──────── */
export const getSubscriptionPlans = () =>
    request<any[]>("/api/admin/subscription-plans");

export const createSubscriptionPlan = (data: any) =>
    request<any>("/api/admin/subscription-plans", { method: "POST", body: JSON.stringify(data) });

export const updateSubscriptionPlan = (id: string, data: any) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteSubscriptionPlan = (id: string) =>
    request<any>(`/api/admin/subscription-plans/${id}`, { method: "DELETE" });

export const getDriverSubscriptions = (status?: string) =>
    request<any[]>(`/api/admin/driver-subscriptions${status ? `?status=${status}` : ''}`);

/* ── Audit Logs ──────────────────────────── */
export const getAuditLogs = (limit = 50) =>
    request<any[]>(`/api/admin/audit-logs?limit=${limit}`);
