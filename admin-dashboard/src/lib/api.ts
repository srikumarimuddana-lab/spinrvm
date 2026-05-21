// Always use relative URL so /api/* requests route through next.config.ts rewrites.
// Never talk directly to the backend from the browser — that bypasses the proxy and triggers CORS.
const API_BASE = "";

// Import Zustand store for token management
import { useAuthStore } from "@/store/authStore";

// ─── B-P1-8: typed rate-limit error ──────────────────────────────────
// Mirrors shared/api/client.ts's RateLimitError so admin login,
// staff "Sign out everywhere", and admin password-change screens can
// show "try again in N seconds" UX. The contract is pinned in
// docs/runbooks/rate-limits.md.
export class RateLimitError extends Error {
    readonly status = 429;
    retryAfterSeconds: number;
    limit: number | null;
    remaining: number | null;
    resetSeconds: number | null;
    data: any;

    constructor(opts: {
        message: string;
        retryAfterSeconds: number;
        limit: number | null;
        remaining: number | null;
        resetSeconds: number | null;
        data: any;
    }) {
        super(opts.message);
        this.name = "RateLimitError";
        this.retryAfterSeconds = opts.retryAfterSeconds;
        this.limit = opts.limit;
        this.remaining = opts.remaining;
        this.resetSeconds = opts.resetSeconds;
        this.data = opts.data;
    }
}

// RFC 9110 §10.2.3: integer seconds OR HTTP-date. Negative clamped to 0.
const parseRetryAfter = (header: string | null, fallback: number): number => {
    if (!header) return fallback;
    const trimmed = header.trim();
    if (/^\d+$/.test(trimmed)) {
        const seconds = parseInt(trimmed, 10);
        return Number.isFinite(seconds) ? Math.max(seconds, 0) : fallback;
    }
    const dateMs = Date.parse(trimmed);
    if (Number.isFinite(dateMs)) {
        return Math.max(Math.ceil((dateMs - Date.now()) / 1000), 0);
    }
    return fallback;
};

const parseIntHeader = (header: string | null): number | null => {
    if (!header) return null;
    const n = parseInt(header.trim(), 10);
    return Number.isFinite(n) ? n : null;
};

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
    // Get token from Zustand store
    const store = useAuthStore.getState();
    const token = store.token;
    // Don't force JSON Content-Type when the body is FormData — the browser
    // sets multipart/form-data with the right boundary automatically.
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers: Record<string, string> = {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(options.headers as Record<string, string>),
    };
    if (token) headers["Authorization"] = `Bearer ${token}`;
    const method = (options.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && store.csrfToken) {
        headers["X-CSRF-Token"] = store.csrfToken;
    }

    const url = `${API_BASE}${path}`;
    try {
        const res = await fetch(url, { ...options, headers });
        if (process.env.NODE_ENV === "development") {
            console.log(`API Request: ${options.method || 'GET'} ${path} -> ${res.status}`);
        }

        if (res.status === 401) {
            // For the login endpoint, fall through to the !res.ok handler so
            // "Invalid credentials" is shown to the user rather than "Unauthorized".
            if (path !== "/api/admin/auth/login") {
                // Attempt one silent refresh before giving up. The HttpOnly
                // refresh cookie is sent automatically — no need to check for
                // a token in JS state.
                await store.silentRefresh();
                const newToken = useAuthStore.getState().token;
                if (newToken) {
                    const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
                    const retryRes = await fetch(url, { ...options, headers: retryHeaders });
                    if (retryRes.ok) return retryRes.json() as T;
                    if (retryRes.status !== 401) {
                        const retryBody = await retryRes.json().catch(() => ({}));
                        const retryMsg =
                            retryBody.detail ||
                            retryBody.error?.detail ||
                            retryBody.error?.message ||
                            retryBody.message ||
                            retryRes.statusText;
                        throw new Error(retryMsg);
                    }
                }
                useAuthStore.getState().logout();
                if (typeof window !== "undefined") {
                    window.location.href = "/login";
                }
                throw new Error("Unauthorized");
            }
        }

        // B-P1-8: throw typed RateLimitError before the generic !res.ok
        // path so the admin login / staff "Sign out everywhere"
        // screens can render a countdown rather than a generic
        // "Request failed". Always reads body so callers still get
        // the backend's `message`/`detail` for display.
        if (res.status === 429) {
            const body = await res.json().catch(() => ({}));
            const retryAfterSeconds = parseRetryAfter(
                res.headers.get("Retry-After"),
                typeof body?.retry_after === "number" ? body.retry_after : 60,
            );
            const limit =
                parseIntHeader(res.headers.get("RateLimit-Limit")) ??
                (typeof body?.limit === "number" ? body.limit : null);
            const remaining = parseIntHeader(res.headers.get("RateLimit-Remaining"));
            const resetSeconds = parseIntHeader(res.headers.get("RateLimit-Reset"));
            const msg =
                body?.message ||
                body?.detail ||
                body?.error?.message ||
                body?.error?.detail ||
                "Too many requests";
            throw new RateLimitError({
                message: msg,
                retryAfterSeconds,
                limit,
                remaining,
                resetSeconds,
                data: body,
            });
        }

        if (!res.ok) {
            const body = await res.json().catch(() => ({}));
            if (process.env.NODE_ENV === "development") {
                console.error(`API Error: ${path}`, body);
            }
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
        if (process.env.NODE_ENV === "development") {
            console.error(`API Request Failed: ${url}`, err);
        }
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
    access_expires_at: string;
    csrf_token?: string;
    user: {
        id: string;
        email: string;
        role: string;
        first_name?: string;
        last_name?: string;
        modules?: string[];
    };
}

export interface AdminMfaRequired {
    mfa_required: true;
    mfa_token: string;
}

export type AdminLoginResult = AdminLoginResponse | AdminMfaRequired;

export const loginAdmin = (phone: string, code: string) =>
    request<AuthResponse>("/api/auth/verify-otp", {
        method: "POST",
        body: JSON.stringify({ phone, code }),
    });

export const loginAdminSession = (email: string, password: string) =>
    request<AdminLoginResult>("/api/admin/auth/login", {
        method: "POST",
        body: JSON.stringify({ email, password }),
    });

export const mfaChallenge = (mfa_token: string, totp_code: string) =>
    request<AdminLoginResponse>("/api/admin/auth/mfa/challenge", {
        method: "POST",
        body: JSON.stringify({ mfa_token, totp_code }),
    });

export const mfaStatus = () =>
    request<{ mfa_enabled: boolean; available: boolean }>("/api/admin/auth/mfa/status");

export const mfaEnroll = () =>
    request<{ secret: string; otpauth_uri: string }>("/api/admin/auth/mfa/enroll", { method: "POST" });

export const mfaConfirm = (totp_code: string) =>
    request<{ backup_codes: string[] }>("/api/admin/auth/mfa/confirm", {
        method: "POST",
        body: JSON.stringify({ totp_code }),
    });

export const mfaDisable = (totp_code: string, password: string) =>
    request<{ success: boolean }>("/api/admin/auth/mfa/disable", {
        method: "POST",
        body: JSON.stringify({ totp_code, password }),
    });

export const sendOtp = (phone: string) =>
    request<{ success: boolean }>("/api/auth/send-otp", {
        method: "POST",
        body: JSON.stringify({ phone }),
    });

// Admin "sign out everywhere" — closes B-P1-13. Bumps
// admin_staff.token_version (kills all in-flight admin access tokens
// on next request) and revokes every refresh token for this staff row.
// Refused server-side for admin-001 (env-var super admin); rotate
// ADMIN_PASSWORD to globally kill that account.
export const logoutAllAdmin = () =>
    request<{ success: boolean; revoked_refresh_tokens: number }>("/api/admin/auth/logout-all", {
        method: "POST",
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
export const getRides = (
    limit = 50,
    offset = 0,
    opts?: { isScheduled?: boolean; status?: string },
) => {
    const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
    if (opts?.isScheduled !== undefined) params.set("is_scheduled", String(opts.isScheduled));
    if (opts?.status) params.set("status", opts.status);
    return request<{ rides: any[]; total_count: number; limit: number; offset: number }>(
        `/api/admin/rides?${params.toString()}`,
    );
};
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
export const sendRideInvoice = (rideId: string) =>
    request<any>(`/api/v1/rides/${rideId}/process-payment`, {
        method: "POST",
        body: JSON.stringify({ tip_amount: 0 }),
    });
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

/* ── Drivers ──────────────────────────────── */
export const getDrivers = (opts: {
    limit?: number;
    offset?: number;
    is_verified?: boolean;
    is_online?: boolean;
    is_available?: boolean;
    status?: string;
    service_area_id?: string;
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.is_verified != null) sp.set("is_verified", String(opts.is_verified));
    if (opts.is_online != null) sp.set("is_online", String(opts.is_online));
    if (opts.is_available != null) sp.set("is_available", String(opts.is_available));
    if (opts.status) sp.set("status", opts.status);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    if (opts.search) sp.set("search", opts.search);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/drivers${qs ? `?${qs}` : ""}`);
};

/** POST-based typeahead search — keeps search terms (may include phone digits) out of URL/server logs. */
export const adminSearchDrivers = (opts: {
    search: string;
    limit?: number;
    is_online?: boolean;
    is_available?: boolean;
}) =>
    request<any[]>("/api/admin/drivers/search", {
        method: "POST",
        body: JSON.stringify(opts),
    });

/** POST-based typeahead search — keeps search terms out of URL/server logs. */
export const adminSearchUsers = (opts: {
    search: string;
    role?: "all" | "rider" | "driver" | "admin";
    limit?: number;
}) =>
    request<any[]>("/api/admin/users/search", {
        method: "POST",
        body: JSON.stringify(opts),
    });
export const getDriverRides = (id: string) =>
    request<any>(`/api/admin/drivers/${id}/rides`);

export interface DriverLiveStats {
    total_rides: number;
    total_earnings: number;
    avg_rating: number | null;
    acceptance_rate: number | null;
    cancelled_by_driver: number;
    total_assigned: number;
}
export const getDriverLiveStats = (id: string) =>
    request<DriverLiveStats>(`/api/admin/drivers/${id}/live-stats`);

export interface DriverPayoutSummary {
    summary: {
        lifetime_earnings: number;
        lifetime_tips: number;
        ytd_earnings: number;
        total_paid_out: number;
        pending_in_flight: number;
        pending_balance: number;
        on_hold: number;
        rides_count: number;
        active_days_30d: number;
        last_payout: {
            id: string;
            amount: number;
            processed_at: string | null;
            bank_name: string | null;
            account_last4: string | null;
        } | null;
        last_failed_payout: {
            id: string;
            amount: number;
            error_message: string | null;
            created_at: string;
        } | null;
    };
    payment_method: {
        has_bank_account: boolean;
        bank_name: string | null;
        account_last4: string | null;
        account_holder_name: string | null;
        account_type: string | null;
        is_verified: boolean | null;
        stripe_connected: boolean;
        stripe_account_hint: string | null;
    };
    payouts: Array<{
        id: string;
        amount: number;
        status: "pending" | "processing" | "completed" | "failed" | string;
        stripe_payout_id: string | null;
        bank_name: string | null;
        account_last4: string | null;
        error_message: string | null;
        created_at: string;
        processed_at: string | null;
    }>;
    // Stripe Connect KYC + tax identity mirror (migration 92).
    // SIN itself is never exposed here — only id_number_provided and
    // last4. Use /reveal-sin for the one-shot retrieval.
    kyc: {
        details_submitted: boolean;
        charges_enabled: boolean;
        payouts_enabled: boolean;
        verification_status: string | null;
        business_type: string | null;
        id_number_provided: boolean;
        id_number_last4: string | null;
        gst_hst_number: string | null;
        requirements_due: string[];
        requirements_past_due: string[];
        disabled_reason: string | null;
        tos_accepted_at: string | null;
        last_synced_at: string | null;
    };
}
export const getDriverPayoutsSummary = (id: string) =>
    request<DriverPayoutSummary>(`/api/admin/drivers/${id}/payouts-summary`);

export const refreshDriverStripeKyc = (id: string) =>
    request<{ status: string }>(`/api/admin/drivers/${id}/refresh-stripe-kyc`, { method: "POST" });

export interface RevealSinResponse {
    sin: string;
    sin_last4: string;
    audit_log_id: string | null;
    warning: string;
}
export const revealDriverSin = (id: string) =>
    request<RevealSinResponse>(`/api/admin/drivers/${id}/reveal-sin`, { method: "POST" });

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
    request<{ message: string; audit_log_id?: string }>("/api/admin/settings", {
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

/* ── Driver Document Verification ────────── */
export const getDriverDocuments = (driverId: string) =>
    request<any[]>(`/api/admin/documents/drivers/${driverId}`);

export type RejectTemplate =
    | "blurry_image"
    | "wrong_document_type"
    | "expired"
    | "information_unclear"
    | "other";

export interface ReviewDocumentOptions {
    notify?: boolean;
    notifyTemplate?: RejectTemplate;
}

export const reviewDocument = (
    docId: string,
    status: string,
    reason?: string,
    expiryDate?: string,
    options?: ReviewDocumentOptions,
) =>
    request<any>(`/api/admin/documents/${docId}/review`, {
        method: "POST",
        body: JSON.stringify({
            status,
            rejection_reason: reason,
            expiry_date: expiryDate,
            ...(options?.notify !== undefined ? { notify: options.notify } : {}),
            ...(options?.notifyTemplate ? { notify_template: options.notifyTemplate } : {}),
        }),
    });

export interface ApprovalQueueItem {
    driver_id: string;
    user_id: string | null;
    first_name: string;
    last_name: string;
    name: string;
    email: string | null;
    phone: string | null;
    profile_photo_url: string | null;
    status: string;
    created_at: string | null;
    queue_started_at: string | null;
    time_in_queue_seconds: number;
    pending_docs_count: number;
    missing_docs_count: number;
    service_area_id: string | null;
    service_area_name: string | null;
    vehicle_type_id: string | null;
    vehicle_type_name: string | null;
}

export interface ApprovalQueueResponse {
    stats: {
        total_pending: number;
        oldest_in_queue_hours: number;
        median_wait_hours: number;
        over_24h_count: number;
    };
    items: ApprovalQueueItem[];
}

export const getApprovalQueue = (params?: {
    limit?: number;
    service_area_id?: string;
}) => {
    const q = new URLSearchParams();
    if (params?.limit) q.set("limit", String(params.limit));
    if (params?.service_area_id) q.set("service_area_id", params.service_area_id);
    const qs = q.toString();
    return request<ApprovalQueueResponse>(
        `/api/admin/drivers/approval-queue${qs ? `?${qs}` : ""}`,
    );
};

export interface ExpiringDocItem {
    driver_id: string;
    user_id: string | null;
    name: string;
    first_name: string;
    last_name: string;
    email: string | null;
    phone: string | null;
    profile_photo_url: string | null;
    status: string | null;
    service_area_id: string | null;
    service_area_name: string | null;
    doc_type: string;
    doc_label: string;
    doc_field: string;
    expiry_date: string;
    days_remaining: number;
    rides_last_30d: number;
    last_nudged_at: string | null;
}

export const getExpiringDocs = (params?: {
    window_days?: 7 | 14 | 30 | number;
    service_area_id?: string;
}) => {
    const q = new URLSearchParams();
    if (params?.window_days) q.set("window_days", String(params.window_days));
    if (params?.service_area_id) q.set("service_area_id", params.service_area_id);
    const qs = q.toString();
    return request<{ items: ExpiringDocItem[] }>(
        `/api/admin/drivers/expiring${qs ? `?${qs}` : ""}`,
    );
};

export const nudgeDriverExpiry = (
    driverId: string,
    body: { doc_type: string; doc_label?: string; custom_message?: string },
) =>
    request<{ ok: boolean }>(`/api/admin/drivers/${driverId}/nudge-expiry`, {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Corporate Accounts ─────────────────────── */
export type CompanyStatus =
    | "pending_verification"
    | "active"
    | "suspended"
    | "closed";

export type SizeTier = "smb" | "mid_market" | "enterprise";

export interface CorporateAccount {
    id: string;
    name: string;
    legal_name?: string | null;
    business_number?: string | null;
    tax_region?: string | null;
    billing_email?: string | null;
    contact_name?: string | null;
    contact_email?: string | null;
    contact_phone?: string | null;
    status: CompanyStatus;
    size_tier: SizeTier;
    kyb_document_url?: string | null;
    kyb_reviewed_at?: string | null;
    kyb_reviewed_by?: string | null;
    credit_limit?: number;
    is_active: boolean;
    created_at: string;
    updated_at: string;
}

export const getCorporateAccounts = () =>
    request<CorporateAccount[]>("/api/admin/corporate-accounts");

export const listCorporateAccounts = (opts: {
    status?: CompanyStatus;
    size_tier?: SizeTier;
    search?: string;
    skip?: number;
    limit?: number;
} = {}) => {
    const p = new URLSearchParams();
    if (opts.status) p.set("status", opts.status);
    if (opts.size_tier) p.set("size_tier", opts.size_tier);
    if (opts.search) p.set("search", opts.search);
    if (opts.skip != null) p.set("skip", String(opts.skip));
    if (opts.limit != null) p.set("limit", String(opts.limit));
    const qs = p.toString();
    return request<CorporateAccount[]>(
        `/api/admin/corporate-accounts${qs ? `?${qs}` : ""}`
    );
};

export const reviewKyb = (id: string, decision: { approve: boolean; note?: string }) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/kyb-review`, {
        method: "POST",
        body: JSON.stringify(decision),
    });

export const getCorporateAccount = (id: string) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`);

export const changeCompanyStatus = (
    id: string,
    transition: { status: CompanyStatus; reason?: string }
) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}/status`, {
        method: "POST",
        body: JSON.stringify(transition),
    });

export const createCorporateAccount = (data: any) =>
    request<CorporateAccount>("/api/admin/corporate-accounts", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const updateCorporateAccount = (id: string, data: any) =>
    request<CorporateAccount>(`/api/admin/corporate-accounts/${id}`, {
        method: "PUT",
        body: JSON.stringify(data),
    });

export const deleteCorporateAccount = (id: string) =>
    request<any>(`/api/admin/corporate-accounts/${id}`, { method: "DELETE" });

/* ── Corporate Wallet ─────────────────────── */
export interface WalletTxn {
    id: string;
    type: string;
    scope: string;
    amount: string;
    balance_after: string;
    created_at: string;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
}

export interface CorporateWallet {
    id: string;
    company_id: string;
    balance: string;
    currency: string;
    auto_topup_enabled: boolean;
    auto_topup_threshold: string | null;
    auto_topup_amount: string | null;
    auto_topup_daily_cap: string;
    soft_negative_floor: string;
    transactions: WalletTxn[];
}

export type WalletConfigPatch = Partial<
    Pick<
        CorporateWallet,
        | "auto_topup_enabled"
        | "auto_topup_threshold"
        | "auto_topup_amount"
        | "auto_topup_daily_cap"
    >
>;

export const getCorporateWallet = (companyId: string) =>
    request<CorporateWallet>(`/api/admin/corporate-accounts/${companyId}/wallet`);

export const updateWalletConfig = (companyId: string, patch: WalletConfigPatch) =>
    request<CorporateWallet>(
        `/api/admin/corporate-accounts/${companyId}/wallet/config`,
        { method: "PUT", body: JSON.stringify(patch) }
    );

export const walletTopupIntent = (
    companyId: string,
    body: { amount: number; payment_method_id?: string }
) =>
    request<{ payment_intent_id: string; client_secret: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/topup`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const walletAdjust = (
    companyId: string,
    body: { amount: number; notes: string }
) =>
    request<{ transaction_id: string; balance_after: string }>(
        `/api/admin/corporate-accounts/${companyId}/wallet/adjust`,
        { method: "POST", body: JSON.stringify(body) }
    );

/* ── Corporate members / allowances (Plan 3) ── */
export type CorporateMemberRole = "owner" | "admin" | "member";
export type CorporateMemberStatus = "invited" | "active" | "suspended" | "removed";
export type AllowanceTypeValue = "fixed_recurring" | "one_time" | "unlimited";

export interface CorporateMember {
    id: string;
    company_id: string;
    user_id?: string | null;
    role: CorporateMemberRole;
    status: CorporateMemberStatus;
    invited_email?: string | null;
    created_at?: string;
    updated_at?: string;
}

export interface CorporateAllowance {
    id: string;
    member_id: string;
    type: AllowanceTypeValue;
    amount?: number | null;
    used: number;
    period_start?: string | null;
    period_end?: string | null;
    rollover?: boolean;
    auto_approve_topup_amount?: number | null;
    auto_approve_monthly_count?: number | null;
    status: "active" | "paused" | "expired";
}

export interface AllowanceRequestRow {
    id: string;
    member_id: string;
    amount: number;
    reason: string;
    status: "pending" | "approved" | "denied" | "auto_approved";
    reviewed_by?: string | null;
    decision_notes?: string | null;
    created_at?: string;
}

export const listCompanyMembers = (companyId: string, status?: string) =>
    request<CorporateMember[]>(
        `/company/${companyId}/members${status ? `?status=${encodeURIComponent(status)}` : ""}`
    );

export const inviteCompanyMember = (
    companyId: string,
    body: { email: string; role: CorporateMemberRole; policy_override?: boolean }
) =>
    request<{ member: CorporateMember; invite_url: string }>(
        `/company/${companyId}/members/invite`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const removeCompanyMember = (companyId: string, memberId: string) =>
    request<CorporateMember>(`/company/${companyId}/members/${memberId}`, {
        method: "DELETE",
    });

export const getMemberAllowance = (companyId: string, memberId: string) =>
    request<CorporateAllowance | Record<string, never>>(
        `/company/${companyId}/members/${memberId}/allowance`
    );

export const putMemberAllowance = (
    companyId: string,
    memberId: string,
    body: {
        type: AllowanceTypeValue;
        amount?: number | null;
        period_start?: string | null;
        period_end?: string | null;
        rollover?: boolean;
        auto_approve_topup_amount?: number | null;
        auto_approve_monthly_count?: number | null;
    }
) =>
    request<CorporateAllowance>(
        `/company/${companyId}/members/${memberId}/allowance`,
        { method: "PUT", body: JSON.stringify(body) }
    );

export const listCompanyAllowanceRequests = (companyId: string, status = "pending") =>
    request<AllowanceRequestRow[]>(
        `/company/${companyId}/allowance-requests?status=${encodeURIComponent(status)}`
    );

export const decideAllowanceRequest = (
    companyId: string,
    requestId: string,
    body: { approve: boolean; note?: string }
) =>
    request<AllowanceRequestRow>(
        `/company/${companyId}/allowance-requests/${requestId}/decide`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const updateCompanyMember = (
    companyId: string,
    memberId: string,
    body: { role?: CorporateMemberRole; status?: CorporateMemberStatus; policy_override?: boolean }
) =>
    request<CorporateMember>(
        `/company/${companyId}/members/${memberId}`,
        { method: "PATCH", body: JSON.stringify(body) }
    );

/* ── Company policy (Plan 6) ── */
export type PaymentSourcePolicy = "allowance_only" | "master_only" | "both";

export interface TimeWindowPolicy {
    day: "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
    start: string;
    end: string;
}

export interface CorporatePolicy {
    id?: string;
    company_id?: string;
    active: boolean;
    max_fare_per_ride?: number | null;
    allowed_geofence?: Record<string, unknown> | null;
    allowed_time_windows?: TimeWindowPolicy[] | null;
    allowed_payment_source: PaymentSourcePolicy;
}

export const getCompanyPolicy = (companyId: string) =>
    request<CorporatePolicy | Record<string, never>>(`/company/${companyId}/policy`);

export const putCompanyPolicy = (
    companyId: string,
    body: Omit<CorporatePolicy, "id" | "company_id">
) =>
    request<CorporatePolicy>(`/company/${companyId}/policy`, {
        method: "PUT",
        body: JSON.stringify(body),
    });

export const patchCompanyPolicy = (
    companyId: string,
    body: Partial<Omit<CorporatePolicy, "id" | "company_id">>
) =>
    request<CorporatePolicy>(`/company/${companyId}/policy`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

/* ── Company allowed domains (Plan 7) ── */
export interface AllowedDomainRow {
    company_id: string;
    domain: string;
}

export const listAllowedDomains = (companyId: string) =>
    request<AllowedDomainRow[]>(`/company/${companyId}/allowed-domains`);

export const addAllowedDomain = (companyId: string, domain: string) =>
    request<AllowedDomainRow>(`/company/${companyId}/allowed-domains`, {
        method: "POST",
        body: JSON.stringify({ domain }),
    });

export const removeAllowedDomain = (companyId: string, domain: string) =>
    request<{ status: string }>(
        `/company/${companyId}/allowed-domains/${encodeURIComponent(domain)}`,
        { method: "DELETE" }
    );

/* ── Company billing (Plan 6) ── */
export interface BillingMemberBreakdown {
    member_id: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
}

export interface BillingSummary {
    month: string;
    wallet_balance: number;
    wallet_currency: string;
    ride_count: number;
    allowance_total: number;
    master_total: number;
    total: number;
    avg_fare: number;
    by_member: BillingMemberBreakdown[];
}

export interface BillingLineItem {
    ride_id: string;
    member_id: string;
    source_type: string;
    allowance_debit_amount: number;
    master_fallback_amount: number;
    policy_check_result?: string;
    created_at: string;
}

export interface BillingStatement {
    month: string;
    from: string;
    to: string;
    line_items: BillingLineItem[];
    summary: {
        ride_count: number;
        allowance_total: number;
        master_total: number;
        total: number;
        avg_fare: number;
        by_member: BillingMemberBreakdown[];
    };
}

export interface BillingTransaction {
    id: string;
    type: string;
    amount: number;
    balance_after?: number;
    notes?: string | null;
    ride_id?: string | null;
    member_id?: string | null;
    stripe_payment_intent_id?: string | null;
    created_at: string;
}

export interface BillingTransactionsPage {
    wallet_id: string;
    balance: number;
    currency: string;
    transactions: BillingTransaction[];
}

export const getCompanyBillingSummary = (companyId: string, month?: string) => {
    const qs = month ? `?month=${encodeURIComponent(month)}` : "";
    return request<BillingSummary>(`/company/${companyId}/billing/summary${qs}`);
};

export const getCompanyBillingStatement = (companyId: string, month: string) =>
    request<BillingStatement>(
        `/company/${companyId}/billing/statements/${encodeURIComponent(month)}`
    );

export const getCompanyBillingTransactions = (
    companyId: string,
    skip = 0,
    limit = 50
) =>
    request<BillingTransactionsPage>(
        `/company/${companyId}/billing/transactions?skip=${skip}&limit=${limit}`
    );

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

/* ── Users (Riders + Drivers + Admins) ───────── */
export const getUsers = (role: "all" | "rider" | "driver" | "admin" = "all") =>
    request<any[]>(`/api/admin/users?role=${role}`);

export const getUsersPaginated = (opts: {
    role?: "all" | "rider" | "driver" | "admin";
    search?: string;
    limit?: number;
    offset?: number;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("role", opts.role ?? "all");
    if (opts.search) sp.set("search", opts.search);
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    return request<any[]>(`/api/admin/users?${sp.toString()}`);
};

export const getUserDetails = (id: string) =>
    request<any>(`/api/admin/users/${id}`);

export const updateUserStatus = (id: string, statusData: any) =>
    request<any>(`/api/admin/users/${id}/status`, {
        method: "PUT",
        body: JSON.stringify(statusData),
    });

/* ── Wallet (admin) ─────────────────────────── */
export const getUserWallet = (userId: string, limit = 50) =>
    request<{
        user: { id: string; name: string; phone: string; email: string };
        wallet: { id: string; balance: number; currency: string; is_active: boolean };
        transactions: Array<{
            id: string;
            type: string;
            amount: number;
            balance_after: number;
            description: string | null;
            reference_id: string | null;
            metadata: Record<string, any>;
            created_at: string;
        }>;
    }>(`/api/admin/wallet/${userId}?limit=${limit}`);

export const creditUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/credit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });

export const debitUserWallet = (userId: string, amount: number, reason: string) =>
    request<{ balance: number; transaction_id: string; audit_log_id?: string }>(`/api/admin/wallet/debit`, {
        method: "POST",
        body: JSON.stringify({ user_id: userId, amount, reason }),
    });

/* ── Promotions ─────────────────────────────── */
export const getPromotions = (opts: {
    limit?: number;
    offset?: number;
    promo_type?: "public" | "private";
    status?: "active" | "inactive" | "not_expired" | "expired";
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.promo_type) sp.set("promo_type", opts.promo_type);
    if (opts.status) sp.set("status", opts.status);
    if (opts.search) sp.set("search", opts.search);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/promotions${qs ? `?${qs}` : ""}`);
};

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
export const getDisputes = (opts: { limit?: number; offset?: number; status?: string } = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit != null) sp.set("limit", String(opts.limit));
    if (opts.offset != null) sp.set("offset", String(opts.offset));
    if (opts.status && opts.status !== "all") sp.set("status", opts.status);
    const qs = sp.toString();
    return request<any[]>(`/api/admin/disputes${qs ? `?${qs}` : ""}`);
};

export const getDisputeStats = () =>
    request<{ open: number; under_review: number; resolved: number; rejected: number; total_refunded: number }>(
        "/api/admin/disputes/stats"
    );

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

/* ── Support Tickets ────────────────────────── */
export const getTickets = (opts: {
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
    return request<any[]>(`/api/admin/tickets${qs ? `?${qs}` : ""}`);
};

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

export const overrideDriverStatus = (driverId: string, status: string, reason?: string) =>
    request<any>(`/api/admin/drivers/${driverId}/status-override`, {
        method: "PUT",
        body: JSON.stringify({ status, reason }),
    });

export const exportDrivers = () =>
    request<{ drivers: any[]; count: number }>("/api/admin/export/drivers");

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
export const getAuditLogs = (opts: {
    limit?: number;
    offset?: number;
    action?: string;
    entity_type?: string;
    search?: string;
} = {}) => {
    const sp = new URLSearchParams();
    sp.set("limit", String(opts.limit ?? 50));
    sp.set("offset", String(opts.offset ?? 0));
    if (opts.action) sp.set("action", opts.action);
    if (opts.entity_type) sp.set("entity_type", opts.entity_type);
    if (opts.search) sp.set("search", opts.search);
    return request<any[]>(`/api/admin/audit-logs?${sp.toString()}`);
};

/* ── Quests / Bonus Challenges ──────────── */
export const getQuests = (isActive?: boolean) =>
    request<any[]>(`/api/v1/quests/admin/list${isActive !== undefined ? `?is_active=${isActive}` : ''}`);

export const createQuest = (data: any) =>
    request<any>("/api/v1/quests/admin/create", { method: "POST", body: JSON.stringify(data) });

export const updateQuest = (id: string, data: any) =>
    request<any>(`/api/v1/quests/admin/${id}`, { method: "PATCH", body: JSON.stringify(data) });

export const getQuestParticipants = (questId: string) =>
    request<any[]>(`/api/v1/quests/admin/${questId}/participants`);

/* ── Analytics ──────────────────────────── */
export const getAnalyticsOverview = (dateRange = "30d") =>
    request<any>(`/api/admin/analytics/overview?date_range=${dateRange}`);

export const getCancellationBreakdown = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/cancellation-reasons?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverAcceptanceRates = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/driver-acceptance?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDemandForecast = (hoursAhead = 24, areaId?: string) =>
    request<any>(`/api/admin/analytics/demand-forecast?hours_ahead=${hoursAhead}${areaId ? `&area_id=${areaId}` : ''}`);

export const getDemandForecastSummary = (areaId?: string) =>
    request<any>(`/api/admin/analytics/demand-forecast/summary${areaId ? `?area_id=${areaId}` : ''}`);

export const getSurgeHistory = (areaId: string, hours = 24) =>
    request<any>(`/api/admin/analytics/surge-history?area_id=${areaId}&hours=${hours}`);

/* ── Payouts ────────────────────────────── */
export const getPayouts = (status?: string) =>
    request<any[]>(`/api/admin/payouts${status ? `?status=${status}` : ''}`);

export const getPayoutById = (id: string) =>
    request<any>(`/api/admin/payouts/${id}`);

export const getPayoutStats = () =>
    request<any>("/api/admin/payouts/stats");

/* ── Disputes (resolve) ─────────────────── */
export const resolveDispute = (id: string, data: { resolution: string; refund_amount?: number; admin_note?: string }) =>
    request<any>(`/api/admin/disputes/${id}/resolve`, { method: "PUT", body: JSON.stringify(data) });

/* ── Live Ride Monitoring ───────────────── */
export const getActiveRides = () =>
    request<any>("/api/admin/rides/active");

export const getMonitoringDrivers = () =>
    request<any[]>("/api/admin/monitoring/drivers");

export const getMonitoringRides = () =>
    request<any[]>("/api/admin/monitoring/rides");

export const adminCancelRide = (rideId: string, reason?: string) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        `/api/admin/rides/${rideId}/cancel`,
        {
            method: "POST",
            body: JSON.stringify({ reason: reason ?? "Cancelled by admin" }),
        },
    );

export const adminCompleteRide = (rideId: string) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        `/api/admin/rides/${rideId}/complete`,
        { method: "POST" },
    );

export interface AdminPlaceBias {
    lat: number;
    lng: number;
    /** Soft-bias radius in metres. Backend clamps to [1000, 100000]. Defaults to 20km. */
    radiusMeters?: number;
}

export const adminPlacesAutocomplete = (
    input: string,
    sessionToken?: string,
    bias?: AdminPlaceBias | null,
) => {
    const sp = new URLSearchParams({ input });
    if (sessionToken) sp.set("session_token", sessionToken);
    if (bias && Number.isFinite(bias.lat) && Number.isFinite(bias.lng)) {
        // 4 decimals (~11m) keeps the URL short and avoids over-precise GPS
        // appearing in proxy access logs.
        sp.set("location", `${bias.lat.toFixed(4)},${bias.lng.toFixed(4)}`);
        sp.set("radius", String(Math.min(Math.max(bias.radiusMeters ?? 20000, 1000), 100000)));
    }
    return request<{ predictions: any[] }>(`/api/admin/places/autocomplete?${sp.toString()}`);
};

export const adminPlacesDetails = (placeId: string, sessionToken?: string) => {
    const sp = new URLSearchParams({ place_id: placeId });
    if (sessionToken) sp.set("session_token", sessionToken);
    return request<{ lat: number; lng: number; formatted_address: string }>(`/api/admin/places/details?${sp.toString()}`);
};

export interface AdminFareEstimateResponse {
    base_fare: number;
    distance_fare: number;
    time_fare: number;
    booking_fee: number;
    surge_multiplier: number;
    subtotal: number;
    area_fees: Array<{ name: string; amount: number }>;
    area_fees_total: number;
    tax_amount: number;
    tax_breakdown: Array<{ name: string; rate: number; amount: number }>;
    grand_total: number;
    service_area: string | null;
}

export const adminFareEstimate = (params: {
    pickup_lat: number;
    pickup_lng: number;
    dropoff_lat: number;
    dropoff_lng: number;
    distance_km: number;
    duration_minutes: number;
    vehicle_type_id: string;
}) => {
    const sp = new URLSearchParams();
    sp.set("pickup_lat", String(params.pickup_lat));
    sp.set("pickup_lng", String(params.pickup_lng));
    sp.set("dropoff_lat", String(params.dropoff_lat));
    sp.set("dropoff_lng", String(params.dropoff_lng));
    sp.set("distance_km", String(params.distance_km));
    sp.set("duration_minutes", String(params.duration_minutes));
    sp.set("vehicle_type_id", params.vehicle_type_id);
    return request<AdminFareEstimateResponse>(`/api/admin/rides/fare-estimate?${sp.toString()}`);
};

export const adminPromoPreview = (data: {
    rider_id: string;
    code: string;
    // String preserves Decimal precision over the wire.
    ride_fare: string;
}) =>
    request<{
        valid: boolean;
        code: string;
        discount_type: string;
        discount_amount: number;
        promo_id: string;
        description: string;
    }>("/api/admin/promo/preview", {
        method: "POST",
        body: JSON.stringify(data),
    });

export interface AdminVehicleType {
    id: string;
    name: string;
    icon?: string;
    capacity?: number;
    is_active: boolean;
}

export const adminListVehicleTypes = () =>
    request<AdminVehicleType[]>("/api/admin/vehicle-types");

export const adminCreateRide = (data: {
    rider_id: string;
    driver_id?: string;
    pickup_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_address: string;
    dropoff_lat: number;
    dropoff_lng: number;
    // Pass as string to preserve Decimal precision on the backend
    // (Pydantic Decimal accepts string/number; string avoids float drift).
    total_fare?: string | number;
    vehicle_type_id?: string;
    subtotal_fare?: string | number;
    discount_amount?: string | number;
    promo_code?: string;
    fare_overridden_by_admin?: boolean;
}) =>
    request<{ success: boolean; ride_id: string; status: string }>(
        "/api/admin/rides/create",
        {
            method: "POST",
            body: JSON.stringify(data),
        },
    );

// ── Monitoring: Redis + Infrastructure ────────────────────────────────

export type RedisStats = {
    backend: "redis" | "in_process";
    connected: boolean;
    used_memory_bytes?: number | null;
    used_memory_human?: string;
    maxmemory_bytes?: number | null;
    maxmemory_human?: string;
    maxmemory_policy?: string;
    used_memory_percent?: number | null;
    used_memory_peak_bytes?: number;
    total_keys?: number;
    keyspace_hits_total?: number | null;
    keyspace_misses_total?: number | null;
    hit_rate_percent?: number | null;
    evicted_keys_total?: number;
    expired_keys_total?: number;
    connected_clients?: number;
    uptime_seconds?: number | null;
    total_commands_processed?: number | null;
    error?: string;
};

export type RedisPrefixCount = {
    prefix: string;
    count: number;
    description: string;
    flushable: boolean;
};

export type RedisHealthResponse = {
    stats: RedisStats;
    prefix_counts: RedisPrefixCount[];
    flushable_prefixes: string[];
};

export type InfrastructureStats = {
    replica: {
        hostname: string;
        pid: number;
        uptime_seconds: number;
        python_version: string | null;
    };
    process: {
        rss_bytes: number | null;
        rss_human: string | null;
        cpu_user_seconds: number | null;
        cpu_system_seconds: number | null;
    };
    thread_pool: { max_workers: number | null; note: string };
    db_circuit_breaker: {
        state: "closed" | "open" | "half_open";
        recent_failures: number;
        opened_at_monotonic: number | null;
    };
    redis: {
        connected: boolean;
        used_memory_bytes: number | null;
        used_memory_human: string | null;
        maxmemory_human: string | null;
        used_memory_percent: number | null;
        total_keys: number | null;
        evicted_keys_total: number | null;
    };
    metrics: Record<string, number>;
};

export const getRedisHealth = () =>
    request<RedisHealthResponse>("/api/admin/monitoring/redis");

export const getInfrastructureStats = () =>
    request<InfrastructureStats>("/api/admin/monitoring/infrastructure");

export const flushRedisPrefix = (prefix: string) =>
    request<{ prefix: string; deleted_keys: number; admin_id: string }>(
        "/api/admin/monitoring/redis/flush-prefix",
        {
            method: "POST",
            body: JSON.stringify({ prefix, confirm: "FLUSH" }),
        },
    );

/* ── Document Requirements (A-P4-1) ─────── */
export const getDocumentRequirements = () =>
    request<any[]>("/api/admin/documents/requirements");

export const createDocumentRequirement = (data: {
    name: string;
    description?: string;
    document_type?: string;
    is_required?: boolean;
    applicable_to?: string;
}) => request<any>("/api/admin/documents/requirements", { method: "POST", body: JSON.stringify(data) });

export const updateDocumentRequirement = (id: string, data: Partial<{
    name: string;
    description: string;
    document_type: string;
    is_required: boolean;
    applicable_to: string;
}>) => request<any>(`/api/admin/documents/requirements/${id}`, { method: "PUT", body: JSON.stringify(data) });

export const deleteDocumentRequirement = (id: string) =>
    request<any>(`/api/admin/documents/requirements/${id}`, { method: "DELETE" });

/* ── Pending Documents paginated (A-P4-4) ── */
export const getPendingDocuments = (params?: { limit?: number; cursor?: string; status?: string }) => {
    const sp = new URLSearchParams();
    if (params?.limit) sp.set("limit", String(params.limit));
    if (params?.cursor) sp.set("cursor", params.cursor);
    if (params?.status) sp.set("status", params.status);
    const qs = sp.toString();
    return request<{ items: any[]; next_cursor: string | null }>(`/api/admin/documents/pending${qs ? `?${qs}` : ""}`);
};

/* ── Payout retry (A-P4-2) ──────────────── */
export const retryPayout = (id: string) =>
    request<any>(`/api/admin/payouts/${id}/retry`, { method: "POST" });
