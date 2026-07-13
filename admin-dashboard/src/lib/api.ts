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
    // An explicit Authorization header from the caller wins over the store
    // token. The forced-enrollment flow passes the enrollment-scoped token
    // while a previous account's session may still sit in the Zustand store
    // (account switching on /login) — overwriting it would enroll MFA on the
    // wrong account.
    if (token && !headers["Authorization"]) headers["Authorization"] = `Bearer ${token}`;
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
            // Same for calls that supplied their own Authorization header (the
            // forced-MFA-enrollment flow): silently refreshing would swap in the
            // store session's token — acting as the previously signed-in account
            // in the account-switch case — and the logout()+redirect below would
            // destroy the enrollment flow. Those callers must see the real 401.
            const callerProvidedAuth = Boolean(
                (options.headers as Record<string, string> | undefined)?.["Authorization"],
            );
            if (path !== "/api/admin/auth/login" && !callerProvidedAuth) {
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

// ADMIN_MFA_ENFORCED: password was correct but the account has no MFA yet.
// mfa_token is enrollment-scoped — only /mfa/enroll and /mfa/confirm accept it.
export interface AdminMfaEnrollmentRequired {
    mfa_enrollment_required: true;
    mfa_token: string;
}

export type AdminLoginResult = AdminLoginResponse | AdminMfaRequired | AdminMfaEnrollmentRequired;

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
    request<{ mfa_enabled: boolean; available: boolean; enforced?: boolean }>("/api/admin/auth/mfa/status");

// Confirm also returns full session tokens so first-login enrollment
// (no session yet, only the enrollment-scoped token) lands in the dashboard.
// Settings-flow callers already have a session and can ignore them.
export interface MfaConfirmResponse extends AdminLoginResponse {
    backup_codes: string[];
    refresh_expires_at?: string;
}

// `authToken` carries the enrollment-scoped token during forced first-login
// enrollment; omitted, the session token from the store is used (Settings flow).
// The store token is null in the forced flow, so this header is not overwritten.
export const mfaEnroll = (authToken?: string) =>
    request<{ secret: string; otpauth_uri: string }>("/api/admin/auth/mfa/enroll", {
        method: "POST",
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
    });

export const mfaConfirm = (totp_code: string, authToken?: string) =>
    request<MfaConfirmResponse>("/api/admin/auth/mfa/confirm", {
        method: "POST",
        body: JSON.stringify({ totp_code }),
        ...(authToken ? { headers: { Authorization: `Bearer ${authToken}` } } : {}),
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
    photo_status?: string;
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
    if (opts.photo_status) sp.set("photo_status", opts.photo_status);
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

export const getDriverDailyActivity = (id: string, date?: string) =>
    request<any>(`/api/admin/drivers/${id}/daily-activity${date ? `?date=${date}` : ""}`);

export interface DriverLiveStats {
    total_rides: number;
    total_earnings: number;
    avg_rating: number | null;
    acceptance_rate: number | null;
    cancelled_by_driver: number;
    total_assigned: number;
    // Driver avatar, loaded lazily here so the bulk drivers list no longer
    // has to ship profile_image (a base64 blob for legacy accounts).
    photo_url: string | null;
}
export const getDriverLiveStats = (id: string) =>
    request<DriverLiveStats>(`/api/admin/drivers/${id}/live-stats`);

export interface DriverReferee {
    name: string;
    email: string;
    referred_at: string;
    is_driver: boolean;
    completed_rides: number;
    rides_required: number;
    rides_remaining: number;
    qualified: boolean;
    status: "earned" | "in_progress";
}
export interface DriverReferralSummary {
    referral_code: string;
    total_referrals: number;
    qualified_referrals: number;
    pending_referrals: number;
    referral_earnings: number;
    reward_amount: number;
    rides_required: number;
    referees: DriverReferee[];
    referred_by?: { name: string; code: string } | null;
}
export const getDriverReferrals = (id: string) =>
    request<DriverReferralSummary>(`/api/admin/drivers/${id}/referrals`);

// ─── LMS training integration ───────────────────────────────────────
// The backend matches the driver against the external Spinr LMS by phone
// number and proxies its training record (see routes/admin/drivers.py
// admin_get_driver_training + services/lms_service.py).
export interface DriverTrainingCourse {
    course_title: string | null;
    status: string;
    progress: number;
    enrolled_at: string | null;
    completed_at: string | null;
}
export interface DriverTrainingCertificate {
    certificate_number: string;
    course_title: string;
    final_quiz_score: number | null;
    issued_at: string;
    expires_at: string | null;
    status: "active" | "revoked" | "expired" | string;
}
export interface DriverTrainingQuizAttempt {
    quiz_title: string | null;
    score: number;
    passed: boolean;
    attempted_at: string;
}
export interface DriverTrainingCommunication {
    communication_type: string;
    message_type: string;
    subject: string | null;
    status: string;
    sent_at: string;
}
export interface DriverTraining {
    matched: boolean;
    reason: "no_phone" | "not_found_in_lms" | null;
    phone_last4: string | null;
    lms: {
        driver: {
            id: string;
            full_name: string;
            email: string;
            phone: string | null;
            city: string | null;
            spinr_approved: boolean;
            sgi_approved: boolean;
        };
        training: {
            status: "not_invited" | "invited" | "registered" | "in_progress" | "completed" | string;
            registered: boolean;
            registered_at: string | null;
            completed_at: string | null;
            completion_percentage: number;
            courses: DriverTrainingCourse[];
        };
        certificates: DriverTrainingCertificate[];
        history: {
            quiz_attempts: DriverTrainingQuizAttempt[];
            communications: DriverTrainingCommunication[];
        };
    } | null;
}
export const getDriverTraining = (id: string, refresh = false) =>
    request<DriverTraining>(
        `/api/admin/drivers/${id}/training${refresh ? "?refresh=true" : ""}`,
    );

export interface ReferralLeader {
    driver_id: string;
    driver_code: string;
    name: string;
    total_referrals: number;
    qualified_referrals: number;
    referral_earnings: number;
}
export interface ReferralLeaderboard {
    leaders: ReferralLeader[];
    fleet_total_referrals: number;
    fleet_total_referrers: number;
    reward_amount: number;
    rides_required: number;
}
export const getReferralLeaderboard = (limit = 20) =>
    request<ReferralLeaderboard>(`/api/admin/referrals/leaderboard?limit=${limit}`);

export const getRiderReferralLeaderboard = (limit = 20) =>
    request<ReferralLeaderboard>(`/api/admin/referrals/rider-leaderboard?limit=${limit}`);

export interface ReferralAnalytics {
    source: "driver" | "rider";
    funnel: {
        total_referred: number | null; // null when an area filter is active
        qualified: number;
        redeemed: number;
        processing: number;
        failed: number;
        redemption_rate: number | null;
        total_paid: string;
        referrer_paid: string;
        referee_paid: string;
        avg_paid: string;
    };
    trend: { date: string; redeemed: number; paid: string }[];
    reward_amount: number;
    rides_required: number;
}
export const getReferralAnalytics = (params: {
    source?: "driver" | "rider";
    serviceAreaId?: string | null;
    start?: string | null;
    end?: string | null;
} = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.serviceAreaId) q.set("service_area_id", params.serviceAreaId);
    if (params.start) q.set("start", params.start);
    if (params.end) q.set("end", params.end);
    const qs = q.toString();
    return request<ReferralAnalytics>(`/api/admin/referrals/analytics${qs ? `?${qs}` : ""}`);
};

export interface FailedReferralClaim {
    id: string;
    referee_user_id: string;
    kind: string;
    referrer_name: string;
    referee_name: string;
    referrer_reward: string;
    referee_reward: string;
    created_at: string;
}
export const getFailedReferralClaims = (params: { source?: "driver" | "rider"; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<{ claims: FailedReferralClaim[]; total: number }>(
        `/api/admin/referrals/failed-claims${qs ? `?${qs}` : ""}`,
    );
};
export const requeueFailedReferral = (refereeUserId: string) =>
    request<{ success: boolean; requeued: string }>(
        `/api/admin/referrals/failed-claims/${encodeURIComponent(refereeUserId)}/requeue`,
        { method: "POST" },
    );

export interface ReferralPair {
    id: string;
    referrer_name: string;
    referee_name: string;
    status: string;
    referrer_reward: string;
    referee_reward: string;
    created_at: string;
}
export const getReferralPairs = (params: { source?: "driver" | "rider"; limit?: number } = {}) => {
    const q = new URLSearchParams();
    if (params.source) q.set("source", params.source);
    if (params.limit) q.set("limit", String(params.limit));
    const qs = q.toString();
    return request<{ pairs: ReferralPair[]; total: number }>(
        `/api/admin/referrals/pairs${qs ? `?${qs}` : ""}`,
    );
};

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

export interface EarningsRide {
    ride_id: string;
    ride_code: string | null;
    status: string;
    total_fare: number;
    driver_earnings: number;
    admin_earnings: number;
    tip_amount: number;
    tax_amount: number;
    discount_amount: number;
    surge_multiplier: number;
    stripe_charge_id: string | null;
    driver_id: string | null;
    driver_name: string | null;
    rider_id: string | null;
    rider_name: string | null;
    service_area_id: string | null;
    completed_at: string | null;
    created_at: string | null;
}

export interface EarningsRidesResponse {
    rides: EarningsRide[];
    total: number;
    offset: number;
    limit: number;
}

export const getEarningsRides = (params?: {
    start_date?: string;
    end_date?: string;
    service_area_id?: string;
    limit?: number;
    offset?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    if (params?.service_area_id) sp.set("service_area_id", params.service_area_id);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return request<EarningsRidesResponse>(`/api/admin/earnings/rides${qs ? `?${qs}` : ""}`);
};

export type EarningsPeriod = "7d" | "30d" | "mtd" | "ytd";

export interface MetricWithDelta {
    current: number;
    previous: number;
    /** Null when the previous-window value was 0 — UI shows "—" instead of "+Inf%". */
    delta_pct: number | null;
}

export interface EarningsOverview {
    period: {
        key: EarningsPeriod;
        label: string;
        days: number;
        start: string;
        end: string;
        prev_start: string;
        prev_end: string;
    };
    metrics: {
        // Pass 1 — CEO row
        gbv: MetricWithDelta;
        net_revenue: MetricWithDelta;
        take_rate_pct: MetricWithDelta;
        completed_trips: MetricWithDelta;
        active_riders: MetricWithDelta;
        active_drivers: MetricWithDelta;
        avg_fare: MetricWithDelta;
        spinr_pass_mrr: MetricWithDelta;
        // Pass 2 — operational health
        cancellation_rate_pct: MetricWithDelta;
        cancellation_revenue: MetricWithDelta;
        cancelled_trips: MetricWithDelta;
        refund_amount: MetricWithDelta;
        refund_count: MetricWithDelta;
        promo_spend: MetricWithDelta;
        promo_count: MetricWithDelta;
        surge_revenue: MetricWithDelta;
        gst_collected: MetricWithDelta;
        pst_collected: MetricWithDelta;
    };
    cancellation_breakdown: {
        current: { rider: number; driver: number; system: number };
        previous: { rider: number; driver: number; system: number };
    };
    /** Ops funnel — created_at cohort of rides requested in the window,
     *  each as a current/previous MetricWithDelta. Semantics (cohort basis,
     *  cancelled_by attribution, reached-searching definition) live in
     *  migration 227_earnings_overview_funnel_agg.sql. The cancel splits
     *  share attribution with cancellation_breakdown — the two widgets
     *  always reconcile. */
    ride_funnel: {
        price_searches: MetricWithDelta;
        requested: MetricWithDelta;
        reached_searching: MetricWithDelta;
        completed: MetricWithDelta;
        rider_cancelled: MetricWithDelta;
        driver_cancelled: MetricWithDelta;
        /** Admin/system/no-driver + unattributed — count − rider − driver. */
        system_cancelled: MetricWithDelta;
        cancelled_after_start: MetricWithDelta;
    };
    daily_series: Array<{
        date: string;
        gbv: number;
        trips: number;
        net_revenue: number;
    }>;
}

export const getEarningsOverview = (params: { period: EarningsPeriod; service_area_id?: string }) => {
    const sp = new URLSearchParams({ period: params.period });
    if (params.service_area_id) sp.set("service_area_id", params.service_area_id);
    return request<EarningsOverview>(`/api/admin/earnings/overview?${sp.toString()}`);
};

export const getSubscriptionStats = (params?: { start_date?: string; end_date?: string; service_area_ids?: string }) => {
    const sp = new URLSearchParams();
    if (params?.start_date) sp.set("start_date", params.start_date);
    if (params?.end_date) sp.set("end_date", params.end_date);
    if (params?.service_area_ids) sp.set("service_area_ids", params.service_area_ids);
    return request<any>(`/api/admin/subscription-stats?${sp.toString()}`);
};

/* ── Settings ─────────────────────────────── */
export const getEmailDeliverability = (days = 7) =>
    request<{
        window_days: number;
        total: number;
        by_status: Record<string, number>;
        by_provider: Record<string, number>;
        by_type: Record<string, number>;
        failure_rate: number;
        suppressed_in_window: number;
        suppression_list_size: number;
        recent_failures: Array<{ email_type: string; provider: string; status: string; recipient_user_id: string | null; created_at: string }>;
        recent_suppressions: Array<{ reason: string; detail: string | null; source: string; message_id: string | null; created_at: string }>;
    }>(`/api/admin/monitoring/email-deliverability?days=${days}`);

export const getSettings = () => request<any>("/api/admin/settings");
export const updateSettings = (data: any) =>
    request<{ message: string; audit_log_id?: string }>("/api/admin/settings", {
        method: "PUT",
        body: JSON.stringify(data),
    });

/* ── AI Assistant ─────────────────────────── */
export interface AiCatalogModel { id: string; label: string; }
export interface AiCatalogProvider {
    provider: string;
    label: string;
    key_field: string;
    models: AiCatalogModel[];
}
export const getAiCatalog = () =>
    request<{ providers: AiCatalogProvider[] }>("/api/admin/ai/catalog");

/* Super-admin AI console — chat as a user + view their threads. */
export interface AdminAiChatResponse {
    conversation_id: string;
    message_id: string;
    reply: string;
    actions: any[];
    audience: string;
}
export const adminAiChat = (data: {
    user_id: string;
    message: string;
    conversation_id?: string | null;
    audience?: "rider" | "driver";
}) =>
    request<AdminAiChatResponse>("/api/admin/ai/chat", {
        method: "POST",
        body: JSON.stringify(data),
    });
export const getAdminAiConversations = (userId: string) =>
    request<{ conversations: { id: string; title: string; updated_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations`,
    );
export const getAdminAiMessages = (userId: string, conversationId: string) =>
    request<{ messages: { id: string; role: "user" | "assistant"; content: string; created_at: string }[] }>(
        `/api/admin/ai/users/${userId}/conversations/${conversationId}/messages`,
    );

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
    profile_image_status: string | null;
    is_new_applicant: boolean;
    is_resubmission: boolean;
    has_pending_photo: boolean;
}

export interface ApprovalQueueResponse {
    stats: {
        total_pending: number;
        oldest_in_queue_hours: number;
        median_wait_hours: number;
        over_24h_count: number;
        new_applicants: number;
        resubmissions: number;
        photo_review: number;
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
    kyb_submitted_at?: string | null;
    kyb_review_note?: string | null;
    kyb_last_decision?: "approved" | "rejected" | null;
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

// Blob-fetch the KYB document through the backend streaming endpoint
// (kyb_document_url is a raw PRIVATE-bucket key, not a browser-usable URL).
export async function fetchKybDocumentBlob(id: string): Promise<Blob> {
    const token = useAuthStore.getState().token;
    const res = await fetch(`/api/admin/corporate-accounts/${id}/kyb/view`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
    });
    if (!res.ok) throw new Error(`Could not load document (${res.status})`);
    return res.blob();
}

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
        `/api/company/${companyId}/members${status ? `?status=${encodeURIComponent(status)}` : ""}`
    );

export const inviteCompanyMember = (
    companyId: string,
    body: { email: string; role: CorporateMemberRole; policy_override?: boolean }
) =>
    request<{ member: CorporateMember; invite_url: string }>(
        `/api/company/${companyId}/members/invite`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const removeCompanyMember = (companyId: string, memberId: string) =>
    request<CorporateMember>(`/api/company/${companyId}/members/${memberId}`, {
        method: "DELETE",
    });

export const getMemberAllowance = (companyId: string, memberId: string) =>
    request<CorporateAllowance | Record<string, never>>(
        `/api/company/${companyId}/members/${memberId}/allowance`
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
        `/api/company/${companyId}/members/${memberId}/allowance`,
        { method: "PUT", body: JSON.stringify(body) }
    );

export const listCompanyAllowanceRequests = (companyId: string, status = "pending") =>
    request<AllowanceRequestRow[]>(
        `/api/company/${companyId}/allowance-requests?status=${encodeURIComponent(status)}`
    );

export const decideAllowanceRequest = (
    companyId: string,
    requestId: string,
    body: { approve: boolean; note?: string }
) =>
    request<AllowanceRequestRow>(
        `/api/company/${companyId}/allowance-requests/${requestId}/decide`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const updateCompanyMember = (
    companyId: string,
    memberId: string,
    body: { role?: CorporateMemberRole; status?: CorporateMemberStatus; policy_override?: boolean }
) =>
    request<CorporateMember>(
        `/api/company/${companyId}/members/${memberId}`,
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
    request<CorporatePolicy | Record<string, never>>(`/api/company/${companyId}/policy`);

export const putCompanyPolicy = (
    companyId: string,
    body: Omit<CorporatePolicy, "id" | "company_id">
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PUT",
        body: JSON.stringify(body),
    });

export const patchCompanyPolicy = (
    companyId: string,
    body: Partial<Omit<CorporatePolicy, "id" | "company_id">>
) =>
    request<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

/* ── Company allowed domains (Plan 7) ── */
export interface AllowedDomainRow {
    company_id: string;
    domain: string;
}

export const listAllowedDomains = (companyId: string) =>
    request<AllowedDomainRow[]>(`/api/company/${companyId}/allowed-domains`);

export const addAllowedDomain = (companyId: string, domain: string) =>
    request<AllowedDomainRow>(`/api/company/${companyId}/allowed-domains`, {
        method: "POST",
        body: JSON.stringify({ domain }),
    });

export const removeAllowedDomain = (companyId: string, domain: string) =>
    request<{ status: string }>(
        `/api/company/${companyId}/allowed-domains/${encodeURIComponent(domain)}`,
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
    return request<BillingSummary>(`/api/company/${companyId}/billing/summary${qs}`);
};

export const getCompanyBillingStatement = (companyId: string, month: string) =>
    request<BillingStatement>(
        `/api/company/${companyId}/billing/statements/${encodeURIComponent(month)}`
    );

export const getCompanyBillingTransactions = (
    companyId: string,
    skip = 0,
    limit = 50
) =>
    request<BillingTransactionsPage>(
        `/api/company/${companyId}/billing/transactions?skip=${skip}&limit=${limit}`
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
    is_marketing?: boolean;
    service_area_id?: string;
}) =>
    request<any>("/api/admin/cloud-messaging/send", {
        method: "POST",
        body: JSON.stringify(data),
    });

export const getCloudMessageStats = () =>
    request<any>("/api/admin/cloud-messaging/stats");

export const deleteCloudMessage = (id: string) =>
    request<any>(`/api/admin/cloud-messaging/${id}`, { method: "DELETE" });

/* ── Marketing audience preview + suppression list ──────────────────── */

export const getCloudMessageAudiencePreview = (audience: string, serviceAreaId?: string) => {
    const params = new URLSearchParams({ audience });
    if (serviceAreaId) params.set("service_area_id", serviceAreaId);
    return request<{
        audience: string;
        audience_total: number;
        email_opted_in: number | null;
        sms_opted_in: number | null;
        push_opted_in: number | null;
    }>(`/api/admin/cloud-messaging/audience-preview?${params.toString()}`);
};

export const getMarketingSuppressions = (channel?: string) => {
    const params = new URLSearchParams();
    if (channel) params.set("channel", channel);
    return request<any[]>(`/api/admin/marketing/suppressions?${params.toString()}`);
};

export const addMarketingSuppression = (data: { channel: string; target: string; reason?: string }) =>
    request<any>("/api/admin/marketing/suppressions", { method: "POST", body: JSON.stringify(data) });

export const deleteMarketingSuppression = (id: string) =>
    request<any>(`/api/admin/marketing/suppressions/${id}`, { method: "DELETE" });

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
// NOTE: no `search` param here on purpose — search terms can be phone
// numbers/emails and GET query strings land in browser history and proxy
// logs. Use the POST-based adminSearchUsers for user search.
export const getUsers = (role: "all" | "rider" | "driver" | "admin" = "all") =>
    request<any[]>(`/api/admin/users?role=${role}`);

export const getUsersPaginated = (opts: {
    role?: "all" | "rider" | "driver" | "both" | "admin";
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

export const updateUserFlags = (id: string, flags: { is_rider?: boolean; is_driver?: boolean }) =>
    request<any>(`/api/admin/users/${id}/role`, {
        method: "PATCH",
        body: JSON.stringify(flags),
    });

export const exportUsers = (limit = 1000) =>
    request<{ users: any[]; count: number }>(`/api/admin/export/users?limit=${limit}`);

export const logPiiReveal = (entityType: string, entityId: string) =>
    request<{ ok: boolean }>("/api/admin/audit/pii-reveal", {
        method: "POST",
        body: JSON.stringify({ entity_type: entityType, entity_id: entityId }),
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

/* ── Safety Queue ───────────────────────────── */
export type SafetyStatus = "open" | "in_progress" | "resolved" | "closed" | "duplicate";
export type SafetySeverity = "sev1" | "sev2" | "sev3";
export type SafetyRole = "rider" | "driver" | "system";

export interface SafetyIncident {
    id: string;
    reported_by_user_id: string | null;
    role: SafetyRole;
    category: string;
    description: string;
    status: SafetyStatus;
    severity: SafetySeverity | null;
    ride_id: string | null;
    latitude: number | null;
    longitude: number | null;
    location_accuracy: number | null;
    assigned_to_admin_id: string | null;
    resolved_at: string | null;
    resolved_by: string | null;
    resolution_notes: string | null;
    reported_at: string;
    created_at: string;
    updated_at: string;
    reporter_name?: string | null;
}

export interface SafetyIncidentListResponse {
    items: SafetyIncident[];
    total: number;
    offset: number;
    limit: number;
    open_count: number | null;
}

export interface SafetyIncidentDetail {
    incident: SafetyIncident;
    reporter: {
        id: string | null;
        name: string | null;
        email: string | null;
        phone: string | null;
        role: string | null;
    } | null;
    ride: {
        id: string | null;
        ride_code: string | null;
        status: string | null;
        rider_id: string | null;
        driver_id: string | null;
        pickup_address: string | null;
        dropoff_address: string | null;
        total_fare: number | null;
        started_at: string | null;
        completed_at: string | null;
    } | null;
}

export const getSafetyIncidents = (params?: {
    status?: SafetyStatus;
    severity?: SafetySeverity;
    role?: SafetyRole;
    category?: string;
    ride_id?: string;
    search?: string;
    limit?: number;
    offset?: number;
}) => {
    const sp = new URLSearchParams();
    if (params?.status) sp.set("status", params.status);
    if (params?.severity) sp.set("severity", params.severity);
    if (params?.role) sp.set("role", params.role);
    if (params?.category) sp.set("category", params.category);
    if (params?.ride_id) sp.set("ride_id", params.ride_id);
    if (params?.search) sp.set("search", params.search);
    if (params?.limit != null) sp.set("limit", String(params.limit));
    if (params?.offset != null) sp.set("offset", String(params.offset));
    const qs = sp.toString();
    return request<SafetyIncidentListResponse>(`/api/admin/safety/incidents${qs ? `?${qs}` : ""}`);
};

export const getSafetyIncident = (id: string) =>
    request<SafetyIncidentDetail>(`/api/admin/safety/incidents/${id}`);

export const updateSafetyIncident = (
    id: string,
    body: Partial<{
        status: SafetyStatus;
        severity: SafetySeverity;
        assigned_to_admin_id: string;
        resolution_notes: string;
    }>,
) =>
    request<{ updated: boolean; incident: SafetyIncident }>(
        `/api/admin/safety/incidents/${id}`,
        { method: "PATCH", body: JSON.stringify(body) },
    );


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

export const getDriverVehicleHistory = (driverId: string) =>
    request<{ history: Array<{ id: string; field: string; old_value: string | null; new_value: string | null; changed_by_role: string; created_at: string }> }>(
        `/api/admin/drivers/${driverId}/vehicle-history`,
    );

export const reviewDriverPhoto = (driverId: string, action: "approve" | "reject") =>
    request<{ message: string; profile_image_status: string }>(`/api/admin/drivers/${driverId}/photo-review`, {
        method: "POST",
        body: JSON.stringify({ action }),
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

// Lost-phone recovery: super_admin clears a staff member's MFA so they can
// re-enroll. Backend revokes the target's sessions and audit-logs the action.
export const resetStaffMfa = (id: string) =>
    request<{ success: boolean }>(`/api/admin/staff/${id}/mfa-reset`, { method: "POST" });

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

export const getAdminSubscriptionPayments = (opts: {
    limit?: number;
    offset?: number;
    driver_id?: string;
    plan_id?: string;
    billing_reason?: string;
    start_date?: string;
    end_date?: string;
} = {}) => {
    const sp = new URLSearchParams();
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.offset) sp.set("offset", String(opts.offset));
    if (opts.driver_id) sp.set("driver_id", opts.driver_id);
    if (opts.plan_id) sp.set("plan_id", opts.plan_id);
    if (opts.billing_reason) sp.set("billing_reason", opts.billing_reason);
    if (opts.start_date) sp.set("start_date", opts.start_date);
    if (opts.end_date) sp.set("end_date", opts.end_date);
    return request<any>(`/api/admin/subscription/payments?${sp.toString()}`);
};

/** Download the Spinr Pass invoice PDF for one subscription payment and trigger
 *  a browser save. Returns true on success. The request<T> helper assumes JSON,
 *  so use a raw authed fetch like getRideRouteMapDataUrl. */
export const downloadSubscriptionInvoice = async (paymentId: string): Promise<boolean> => {
    const token = useAuthStore.getState().token;
    try {
        const res = await fetch(`/api/admin/subscription/payments/${paymentId}/invoice.pdf`, {
            headers: token ? { Authorization: `Bearer ${token}` } : {},
        });
        if (!res.ok) return false;
        const blob = await res.blob();
        const cd = res.headers.get("content-disposition") || "";
        const filename = cd.match(/filename="?([^"]+)"?/)?.[1] || `SpinrPass_Invoice_${paymentId}.pdf`;
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = filename;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        return true;
    } catch (e) {
        if (process.env.NODE_ENV === "development") console.log("Invoice download failed:", e);
        return false;
    }
};

/** Email the Spinr Pass invoice to the driver's address on file (admin-triggered). */
export const resendAdminSubscriptionInvoice = (paymentId: string) =>
    request<{ success: boolean }>(`/api/admin/subscription/payments/${paymentId}/resend-invoice`, {
        method: "POST",
    });

export const updateSubscriptionTaxConfig = (
    areaId: string,
    config: { enabled: boolean; province: string; gst_rate: number; pst_rate: number; hst_rate: number },
) =>
    request<any>(`/api/admin/service-areas/${areaId}/subscription-tax`, {
        method: "PUT",
        body: JSON.stringify(config),
    });

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

/** Main-dashboard stat cards aggregated by time window + optional service area. */
export const getDashboardOverview = (opts: { range?: string; service_area_id?: string | null } = {}) => {
    const sp = new URLSearchParams();
    if (opts.range) sp.set("range", opts.range);
    if (opts.service_area_id) sp.set("service_area_id", opts.service_area_id);
    return request<any>(`/api/admin/analytics/dashboard?${sp.toString()}`);
};

export const getCancellationBreakdown = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/cancellation-reasons?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverAcceptanceRates = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/driver-acceptance?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverOfferStats = (dateRange = "30d", serviceAreaId?: string) =>
    request<any>(`/api/admin/analytics/driver-offer-stats?date_range=${dateRange}${serviceAreaId ? `&service_area_id=${serviceAreaId}` : ''}`);

export const getDriverOfferTrends = (dateRange = "30d", opts?: { driverId?: string; serviceAreaId?: string }) => {
    const sp = new URLSearchParams({ date_range: dateRange });
    if (opts?.driverId) sp.set("driver_id", opts.driverId);
    if (opts?.serviceAreaId) sp.set("service_area_id", opts.serviceAreaId);
    return request<{ date_range: string; driver_id: string | null; daily_chart: { date: string; offered: number; accepted: number; declined: number; ignored: number; preempted: number }[] }>(
        `/api/admin/analytics/driver-offer-trends?${sp.toString()}`,
    );
};

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

export interface PayoutsOverview {
    period: {
        key: EarningsPeriod;
        label: string;
        days: number;
        start: string;
        end: string;
        prev_start: string;
        prev_end: string;
    };
    metrics: {
        outstanding_payable: MetricWithDelta;
        total_paid_out: MetricWithDelta;
        pending_in_flight: MetricWithDelta;
        failed_amount: MetricWithDelta;
        success_rate_pct: MetricWithDelta;
        median_time_to_payout_hours: MetricWithDelta;
        avg_payout_amount: MetricWithDelta;
        payouts_count: MetricWithDelta;
    };
    daily_series: Array<{
        date: string;
        paid_out: number;
        pending: number;
        failed: number;
    }>;
    // Pass 2 — operational queues. None carry PoP because they're
    // "right now" lists rather than period totals.
    failure_reasons: Array<{ reason: string; count: number; amount: number }>;
    stuck_over_48h: { count: number; amount: number };
    blocked_drivers: { count: number; outstanding_balance: number };
    top_drivers: Array<{ driver_id: string; name: string; amount: number; payout_count: number }>;
    at_risk_drivers: Array<{ driver_id: string; name: string; failure_count: number; last_reason: string | null }>;
    // Pass 4 — compliance.
    t4a_snapshot: {
        tax_year: number;
        drivers_with_earnings: number;
        buckets: {
            under_500: number;
            from_500_to_10k: number;
            from_10k_to_30k: number;
            over_30k: number;
        };
        ytd_gross_earnings: number;
    };
    period_locks: Array<{
        period: string;
        closed_at: string;
        closed_by: string | null;
        actor_role: string | null;
    }>;
}

export interface ClosePayoutPeriodResponse {
    period: string;
    payout_count: number;
    total_amount: number;
    audit_log_id: string | null;
}
export const closePayoutPeriod = (year: number, month: number) =>
    request<ClosePayoutPeriodResponse>(`/api/admin/payouts/close-period`, {
        method: "POST",
        body: JSON.stringify({ year, month }),
    });

export const getPayoutsOverview = (params: { period: EarningsPeriod; service_area_id?: string }) => {
    const sp = new URLSearchParams({ period: params.period });
    if (params.service_area_id) sp.set("service_area_id", params.service_area_id);
    return request<PayoutsOverview>(`/api/admin/payouts/overview?${sp.toString()}`);
};

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
    marker_variant?: string;
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

export type WebsocketHealth = {
    fanout: {
        active: boolean;
        channel: string;
        backend_scheme: string;
        configured: boolean;
        last_error: string | null;
    };
    connections: { total: number; admins: number; drivers: number; riders: number };
    replica_hostname: string;
    worker_pid: number;
    workers_hint: number | null;
    per_worker: boolean;
};

export const getWebsocketHealth = () =>
    request<WebsocketHealth>("/api/admin/monitoring/websockets");

export type RedisConnectivityProbe = {
    label: string;
    configured: boolean;
    status: "ok" | "degraded" | "error" | "unset";
    endpoint?: string;
    scheme?: string;
    host?: string;
    port?: number | null;
    tls?: boolean;
    ping_ms?: number;
    pubsub?: { ok: boolean; error?: string };
    error?: string;
    warning?: string;
    same_as?: string;
};

// Separate from getRedisHealth on purpose: this opens Redis clients and runs a
// pub/sub round-trip per URL, so it's called only on load + manual refresh,
// never on the 10s poll loop.
export const getRedisConnectivity = () =>
    request<{ connectivity: RedisConnectivityProbe[] }>(
        "/api/admin/monitoring/redis/connectivity",
    );

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

export interface BulkRetryPayoutsRequest {
    payout_ids?: string[];
    since?: string;
    service_area_id?: string;
    max_to_retry?: number;
}
export interface BulkRetryPayoutsResponse {
    retried: number;
    skipped: number;
    failed_to_initiate: number;
    details: Array<{ payout_id: string; status: string; reason?: string }>;
}
export const bulkRetryPayouts = (body: BulkRetryPayoutsRequest) =>
    request<BulkRetryPayoutsResponse>(`/api/admin/payouts/bulk-retry`, {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Zoho Desk support tickets ──────────────────────────────────────────
 * Native admin UI proxied to Zoho Desk via the backend. Secrets are
 * write-only; getZohoConfig returns presence flags only. Gated by the
 * `support_tickets` RBAC module (config endpoints require any admin).
 */
export interface ZohoConfigStatus {
    enabled: boolean;
    auto_sync_enabled?: boolean;
    data_center: string;
    org_id: string;
    default_department_id: string;
    default_from_email: string;
    has_client_id: boolean;
    has_client_secret: boolean;
    has_refresh_token: boolean;
    connected: boolean;
    last_synced_at?: string | null;
    last_sync_count?: number | null;
    updated_at?: string | null;
}
export interface ZohoConfigUpdate {
    enabled?: boolean;
    auto_sync_enabled?: boolean;
    data_center?: string;
    org_id?: string;
    default_department_id?: string;
    default_from_email?: string;
    client_id?: string;
    client_secret?: string;
    refresh_token?: string;
}
export interface ZohoTicketsResponse {
    data: any[];
}
export interface ZohoDashboard {
    total: number | null;
    total_available: boolean;
    open: number;
    by_status: Record<string, number>;
    recent: any[];
    sample_size: number;
    approximate: boolean;
}
export interface ZohoTrends {
    sample_size: number;
    approximate: boolean;
    volume: Array<{ date: string; opened: number; closed: number }>;
    by_status: Record<string, number>;
    by_priority: Record<string, number>;
    by_channel: Record<string, number>;
    by_category: Record<string, number>;
    by_classification: Record<string, number>;
    by_tag: Record<string, number>;
    top_contacts: Array<{ name: string; count: number }>;
    stats: {
        opened: number;
        closed: number;
        open_now: number;
        avg_resolution_hours: number | null;
        median_resolution_hours: number | null;
        resolved_sample: number;
    };
}

export const getZohoConfig = () =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config");
export const updateZohoConfig = (body: ZohoConfigUpdate) =>
    request<ZohoConfigStatus>("/api/admin/support-tickets/config", {
        method: "PUT",
        body: JSON.stringify(body),
    });
export const testZohoConnection = () =>
    request<{ ok: boolean; departments: any[] }>("/api/admin/support-tickets/config/test", {
        method: "POST",
    });
export const syncDeskTickets = () =>
    request<{ upserted?: number; skipped?: string }>("/api/admin/support-tickets/sync", {
        method: "POST",
    });

export const getDeskDashboard = (departmentId?: string) => {
    const qs = departmentId ? `?department_id=${encodeURIComponent(departmentId)}` : "";
    return request<ZohoDashboard>(`/api/admin/support-tickets/dashboard${qs}`);
};
export const getDeskTrends = (opts?: { days?: number; departmentId?: string; assigneeId?: string }) => {
    const sp = new URLSearchParams();
    if (opts?.days) sp.set("days", String(opts.days));
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    const qs = sp.toString();
    return request<ZohoTrends>(`/api/admin/support-tickets/trends${qs ? `?${qs}` : ""}`);
};

export const getDeskTickets = (opts?: {
    from?: number;
    limit?: number;
    status?: string;
    departmentId?: string;
    assigneeId?: string;
    priority?: string;
    channel?: string;
    sortBy?: string;
}) => {
    const sp = new URLSearchParams();
    if (opts?.from) sp.set("from", String(opts.from));
    if (opts?.limit) sp.set("limit", String(opts.limit));
    if (opts?.status) sp.set("status", opts.status);
    if (opts?.departmentId) sp.set("department_id", opts.departmentId);
    if (opts?.assigneeId) sp.set("assignee_id", opts.assigneeId);
    if (opts?.priority) sp.set("priority", opts.priority);
    if (opts?.channel) sp.set("channel", opts.channel);
    if (opts?.sortBy) sp.set("sort_by", opts.sortBy);
    const qs = sp.toString();
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets${qs ? `?${qs}` : ""}`);
};
export interface CreateDeskTicket {
    subject: string;
    description?: string;
    email?: string;
    first_name?: string;
    last_name?: string;
    phone?: string;
    priority?: string;
    channel?: string;
    category?: string;
    department_id?: string;
}
export const createDeskTicket = (body: CreateDeskTicket) =>
    request<any>(`/api/admin/support-tickets/tickets`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const searchDeskTickets = (opts: {
    q: string;
    from?: number;
    limit?: number;
    departmentId?: string;
    status?: string;
    priority?: string;
    assigneeId?: string;
}) => {
    const sp = new URLSearchParams();
    sp.set("q", opts.q);
    if (opts.from) sp.set("from", String(opts.from));
    if (opts.limit) sp.set("limit", String(opts.limit));
    if (opts.departmentId) sp.set("department_id", opts.departmentId);
    if (opts.status) sp.set("status", opts.status);
    if (opts.priority) sp.set("priority", opts.priority);
    if (opts.assigneeId) sp.set("assignee_id", opts.assigneeId);
    return request<ZohoTicketsResponse>(`/api/admin/support-tickets/search?${sp.toString()}`);
};
export const getDeskTicket = (id: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`);
export const getDeskTicketThreads = (id: string) =>
    request<ZohoTicketsResponse>(`/api/admin/support-tickets/tickets/${id}/threads`);
export const getDeskThread = (ticketId: string, threadId: string) =>
    request<any>(`/api/admin/support-tickets/tickets/${ticketId}/threads/${threadId}`);
export const replyDeskTicket = (id: string, body: { content: string; to?: string; channel?: string }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/reply`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const commentDeskTicket = (id: string, body: { content: string; is_public?: boolean }) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}/comment`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const updateDeskTicket = (
    id: string,
    body: {
        status?: string;
        priority?: string;
        assigneeId?: string;
        departmentId?: string;
        category?: string;
        subCategory?: string;
        classification?: string;
    },
) =>
    request<any>(`/api/admin/support-tickets/tickets/${id}`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });
export const updateDeskTicketTags = (id: string, body: { add?: string[]; remove?: string[] }) =>
    request<{ ok: boolean }>(`/api/admin/support-tickets/tickets/${id}/tags`, {
        method: "POST",
        body: JSON.stringify(body),
    });
export const getDeskAgents = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/agents");
export const getDeskDepartments = () =>
    request<ZohoTicketsResponse>("/api/admin/support-tickets/departments");

/* ── Service area (Spinr-local; not synced to Zoho) ─────────────────────── */
export interface DeskServiceArea {
    id: string;
    name?: string;
    city?: string;
    province?: string;
}
export interface DeskTicketServiceArea {
    service_area_id?: string | null;
    service_area_name?: string | null;
    service_area_source?: "auto" | "manual" | null;
    service_area_assigned_at?: string | null;
    service_area_assigned_by?: string | null;
    needs_assignment?: boolean;
    suggested?: { service_area_id: string; service_area_name?: string; matched_user_id?: string } | null;
}
export const getDeskServiceAreas = () =>
    request<{ data: DeskServiceArea[] }>("/api/admin/support-tickets/service-areas");
export const getDeskTicketServiceArea = (id: string) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`);
export const setDeskTicketServiceArea = (id: string, service_area_id: string | null) =>
    request<DeskTicketServiceArea>(`/api/admin/support-tickets/tickets/${id}/service-area`, {
        method: "PUT",
        body: JSON.stringify({ service_area_id }),
    });

/* ── AI reply suggestion (draft only; agent reviews + sends) ────────────── */
export const aiSuggestDeskReply = (id: string, instruction?: string) =>
    request<{ reply: string; provider?: string; model?: string }>(
        `/api/admin/support-tickets/tickets/${id}/ai-suggest-reply`,
        {
            method: "POST",
            // Optional agent guidance steering the draft. Omit the body when
            // there's nothing to add so the endpoint behaves exactly as before.
            ...(instruction && instruction.trim()
                ? { body: JSON.stringify({ instruction: instruction.trim() }) }
                : {}),
        },
    );
