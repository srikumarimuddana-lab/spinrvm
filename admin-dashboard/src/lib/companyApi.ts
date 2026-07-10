// Company-portal API client (Spinr for Business).
//
// Same relative-URL/proxy contract as lib/api.ts, but bound to the
// companyAuthStore rider session: 401s silent-refresh via the HttpOnly
// refresh cookie and bounce to /company-login (never the staff /login).
// Portal pages import the company-session helpers below (NOT from @/lib/api,
// whose identically-pathed helpers run on the staff admin session).

import { useCompanyAuthStore, CompanyMembershipProfile } from "@/store/companyAuthStore";
import type {
    CorporateMember,
    CorporateMemberRole,
    CorporateMemberStatus,
    CorporateAllowance,
    AllowanceTypeValue,
    AllowanceRequestRow,
    CorporatePolicy,
    AllowedDomainRow,
    BillingSummary,
    BillingStatement,
    BillingTransactionsPage,
} from "@/lib/api";

const API_BASE = "";

export async function companyRequest<T>(path: string, options: RequestInit = {}): Promise<T> {
    const store = useCompanyAuthStore.getState();
    const isFormData = typeof FormData !== "undefined" && options.body instanceof FormData;
    const headers: Record<string, string> = {
        ...(isFormData ? {} : { "Content-Type": "application/json" }),
        ...(options.headers as Record<string, string>),
    };
    if (store.token && !headers["Authorization"]) {
        headers["Authorization"] = `Bearer ${store.token}`;
    }
    const method = (options.method ?? "GET").toUpperCase();
    if (!["GET", "HEAD", "OPTIONS"].includes(method) && store.csrfToken) {
        headers["X-CSRF-Token"] = store.csrfToken;
    }

    const url = `${API_BASE}${path}`;
    const res = await fetch(url, { ...options, headers });

    if (res.status === 401) {
        const refreshed = await store.silentRefresh();
        if (refreshed) {
            // Rebuild BOTH auth headers from the refreshed store: the refresh
            // rotated the csrf_token cookie + value, so reusing the stale
            // X-CSRF-Token from `headers` would fail the backend double-submit
            // on write retries (booking/cancel/section).
            const refreshedStore = useCompanyAuthStore.getState();
            const retryHeaders: Record<string, string> = {
                ...headers,
                Authorization: `Bearer ${refreshedStore.token}`,
            };
            if (!["GET", "HEAD", "OPTIONS"].includes(method) && refreshedStore.csrfToken) {
                retryHeaders["X-CSRF-Token"] = refreshedStore.csrfToken;
            }
            const retryRes = await fetch(url, { ...options, headers: retryHeaders });
            if (retryRes.ok) return retryRes.json() as T;
            if (retryRes.status !== 401) {
                const retryBody = await retryRes.json().catch(() => ({}));
                throw new Error(
                    retryBody.detail?.message ?? retryBody.detail ?? retryBody.message ?? retryRes.statusText
                );
            }
        }
        await useCompanyAuthStore.getState().logout();
        if (typeof window !== "undefined") {
            window.location.href = "/company-login";
        }
        throw new Error("Unauthorized");
    }

    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = body.detail;
        const message =
            (typeof detail === "object" && detail?.message) ||
            (typeof detail === "string" && detail) ||
            body.message ||
            res.statusText;
        const err = new Error(message) as Error & { status?: number; detail?: unknown };
        err.status = res.status;
        err.detail = detail;
        throw err;
    }
    return res.json() as T;
}

/* ── Portal auth (phone OTP — rider identity) ── */

export const sendCompanyOtp = async (phone: string): Promise<void> => {
    // Portal auth namespace (App-Check-exempt on the backend; mobile keeps
    // /api/v1/auth/* enforced). Routed via the catch-all /api proxy → backend.
    const res = await fetch("/api/portal/auth/send-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail?.message ?? body.detail ?? body.message ?? "Could not send code");
    }
};

export interface CompanyOtpVerifyResult {
    token: string;
    csrf_token?: string;
    user: { id: string; phone?: string; email?: string; first_name?: string; last_name?: string };
}

export const verifyCompanyOtp = async (phone: string, code: string): Promise<CompanyOtpVerifyResult> => {
    // LOCAL Next route (/api/company-auth/verify-otp) — proxies to the backend
    // and STRIPS the 30-day refresh_token from the JSON body (it lives only in
    // the HttpOnly spinr_company_rt cookie), so the long-lived credential never
    // reaches JS. Mirrors the admin login route.
    const res = await fetch("/api/company-auth/verify-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone, code }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(body.detail?.message ?? body.detail ?? body.message ?? "Invalid code");
    }
    return body as CompanyOtpVerifyResult;
};

/* ── Portal auth (work email OTP — company identity) ── */

export const sendCompanyEmailOtp = async (email: string): Promise<void> => {
    const res = await fetch("/api/portal/auth/send-email-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email }),
    });
    if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail?.message ?? body.detail ?? body.message ?? "Could not send code");
    }
};

export const verifyCompanyEmailOtp = async (
    email: string,
    code: string
): Promise<CompanyOtpVerifyResult> => {
    const res = await fetch("/api/company-auth/verify-email-otp", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email, code }),
    });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
        throw new Error(body.detail?.message ?? body.detail ?? body.message ?? "Invalid code");
    }
    return body as CompanyOtpVerifyResult;
};

// Role-appropriate landing inside a company: owner/admin get the management
// overview (which calls admin-only endpoints); a plain member gets the
// booking flow (the overview would show empty/failed admin metrics for them).
export function portalHome(companyId: string, role?: string): string {
    return role === "owner" || role === "admin"
        ? `/company-portal/${companyId}/overview`
        : `/company-portal/${companyId}/book`;
}

/* ── Self-serve company signup (M1.5) ── */

export interface CompanySignupPayload {
    name: string;
    legal_name: string;
    business_number: string;
    industry?: string | null;
    contact_name: string;
    contact_phone?: string | null;
    address_line1: string;
    address_line2?: string | null;
    city: string;
    province: string;
    postal_code: string;
    terms_accepted: boolean;
    terms_version: string;
}

export interface CompanySignupResult {
    success: boolean;
    company: { id: string; name: string; status: string };
    member: { id: string; role: string };
    message: string;
}

// Authenticated-first: the caller has already completed the email-OTP login,
// so companyRequest attaches the bearer + CSRF token. Identity (work email)
// comes from the session server-side — it is deliberately NOT in the payload.
export const registerCompany = (payload: CompanySignupPayload) =>
    companyRequest<CompanySignupResult>("/api/portal/companies/signup", {
        method: "POST",
        body: JSON.stringify(payload),
    });

/* ── KYB verification (M2.5) ── */

export interface CompanyKybState {
    state: "not_submitted" | "under_review" | "rejected" | "suspended" | "approved" | "closed";
    submitted_at?: string | null;
    reviewed_at?: string | null;
    review_note?: string | null;
    can_resubmit: boolean;
}

export const getCompanyKyb = (companyId: string) =>
    companyRequest<CompanyKybState>(`/api/company/${companyId}/kyb`);

export const getKybUploadUrl = (companyId: string, contentType: string) =>
    companyRequest<{ signed_url: string; path: string; expires_at: string }>(
        `/api/company/${companyId}/kyb/upload-url`,
        { method: "POST", body: JSON.stringify({ content_type: contentType }) }
    );

// Raw PUT to the short-lived signed URL — goes straight to storage, not the
// backend, so companyRequest (auth headers/CSRF) is deliberately not used.
export async function uploadKybFile(signedUrl: string, file: File): Promise<void> {
    const res = await fetch(signedUrl, {
        method: "PUT",
        headers: { "Content-Type": file.type },
        body: file,
    });
    if (!res.ok) throw new Error("Upload failed — please try again.");
}

export const submitKybDocument = (companyId: string, path: string) =>
    companyRequest<{ success: boolean; state: string; submitted_at?: string; resubmitted: boolean }>(
        `/api/company/${companyId}/kyb/submit`,
        { method: "POST", body: JSON.stringify({ path }) }
    );

export const getMyWorkProfiles = () =>
    companyRequest<CompanyMembershipProfile[]>("/api/rider/work-profile");

export const acceptCompanyInvite = (token: string) =>
    companyRequest<{ membership?: unknown; status?: string }>(
        "/api/rider/work-profile/accept-invite",
        { method: "POST", body: JSON.stringify({ token }) }
    );

/* ── Guest bookings ── */

export interface CompanyBookingRow {
    ride_id: string;
    ride_code?: string | null;
    status: string;
    guest_booking: boolean;
    customer_first_name?: string | null;
    booked_by_member_id?: string | null;
    booked_by_name?: string | null;
    section_id?: string | null;
    pickup_address?: string;
    dropoff_address?: string;
    scheduled_time?: string | null;
    created_at?: string;
    grand_total?: number | string | null;
    payment_status?: string | null;
    cancelled_reason?: string | null;
}

export interface CreateBookingBody {
    customer_name: string;
    customer_phone: string;
    pickup_address: string;
    pickup_lat: number;
    pickup_lng: number;
    dropoff_address: string;
    dropoff_lat: number;
    dropoff_lng: number;
    distance_km: number;
    duration_minutes: number;
    vehicle_type_id: string;
    scheduled_time?: string | null;
    rider_notes?: string | null;
}

export interface CreateBookingResult {
    success: boolean;
    booking: CompanyBookingRow;
    tracking_url?: string | null;
    customer_has_app?: boolean;
}

export const createCompanyBooking = (companyId: string, body: CreateBookingBody) =>
    companyRequest<CreateBookingResult>(`/api/company/${companyId}/bookings`, {
        method: "POST",
        body: JSON.stringify(body),
    });

export const listCompanyBookings = (
    companyId: string,
    params: {
        status?: string;
        member_id?: string;
        section_id?: string;
        from?: string;
        to?: string;
        skip?: number;
        limit?: number;
    } = {}
) => {
    const qs = new URLSearchParams();
    Object.entries(params).forEach(([k, v]) => {
        if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
    });
    const q = qs.toString();
    return companyRequest<{ bookings: CompanyBookingRow[]; skip: number; limit: number }>(
        `/api/company/${companyId}/bookings${q ? `?${q}` : ""}`
    );
};

export const cancelCompanyBooking = (companyId: string, rideId: string) =>
    companyRequest<{ success?: boolean }>(
        `/api/company/${companyId}/bookings/${rideId}/cancel`,
        { method: "POST" }
    );

export interface CompanyFareEstimate {
    base_fare: number;
    distance_fare: number;
    time_fare: number;
    booking_fee: number;
    subtotal: number;
    area_fees_total: number;
    tax_amount: number;
    grand_total: number;
    service_area?: string | null;
}

export const companyBookingFareEstimate = (
    companyId: string,
    params: {
        pickup_lat: number;
        pickup_lng: number;
        dropoff_lat: number;
        dropoff_lng: number;
        distance_km: number;
        duration_minutes: number;
        vehicle_type_id: string;
    }
) => {
    const qs = new URLSearchParams(
        Object.fromEntries(Object.entries(params).map(([k, v]) => [k, String(v)]))
    );
    return companyRequest<CompanyFareEstimate>(
        `/api/company/${companyId}/bookings/fare-estimate?${qs.toString()}`
    );
};

/* ── Sections (departments) ── */

export interface CompanySection {
    id: string;
    company_id: string;
    name: string;
    description?: string | null;
    status: "active" | "archived";
    member_count?: number;
    created_at?: string;
}

export const listCompanySections = (companyId: string) =>
    companyRequest<{ sections: CompanySection[] }>(`/api/company/${companyId}/sections`);

export const createCompanySection = (companyId: string, body: { name: string; description?: string }) =>
    companyRequest<CompanySection>(`/api/company/${companyId}/sections`, {
        method: "POST",
        body: JSON.stringify(body),
    });

export const updateCompanySection = (
    companyId: string,
    sectionId: string,
    body: { name?: string; description?: string; status?: "active" | "archived" }
) =>
    companyRequest<CompanySection>(`/api/company/${companyId}/sections/${sectionId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

export const archiveCompanySection = (companyId: string, sectionId: string) =>
    companyRequest<CompanySection>(`/api/company/${companyId}/sections/${sectionId}`, {
        method: "DELETE",
    });

export const assignMemberSection = (companyId: string, memberId: string, sectionId: string | null) =>
    companyRequest<unknown>(`/api/company/${companyId}/members/${memberId}`, {
        method: "PATCH",
        // Backend contract: empty string clears the assignment (None means
        // "field not provided" under its exclude_none handling).
        body: JSON.stringify({ section_id: sectionId ?? "" }),
    });

/* ── Company management (portal, COMPANY session) ──
 *
 * The /company/{id}/** endpoints are guarded by require_company_admin /
 * require_company_member (rider JWT + membership). Portal pages MUST call
 * these company-session versions — the identically-named helpers in @/lib/api
 * run on the STAFF admin session and would 401 a company user to /login.
 */

export const listCompanyMembers = (companyId: string, status?: string) =>
    companyRequest<CorporateMember[]>(
        `/api/company/${companyId}/members${status ? `?status=${encodeURIComponent(status)}` : ""}`
    );

export const inviteCompanyMember = (
    companyId: string,
    body: { email: string; role: CorporateMemberRole; policy_override?: boolean }
) =>
    companyRequest<{ member: CorporateMember; invite_url: string }>(
        `/api/company/${companyId}/members/invite`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const updateCompanyMember = (
    companyId: string,
    memberId: string,
    body: { role?: CorporateMemberRole; status?: CorporateMemberStatus; policy_override?: boolean }
) =>
    companyRequest<CorporateMember>(`/api/company/${companyId}/members/${memberId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

export const getMemberAllowance = (companyId: string, memberId: string) =>
    companyRequest<CorporateAllowance | Record<string, never>>(
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
    companyRequest<CorporateAllowance>(`/api/company/${companyId}/members/${memberId}/allowance`, {
        method: "PUT",
        body: JSON.stringify(body),
    });

export const listCompanyAllowanceRequests = (companyId: string, status = "pending") =>
    companyRequest<AllowanceRequestRow[]>(
        `/api/company/${companyId}/allowance-requests?status=${encodeURIComponent(status)}`
    );

export const decideAllowanceRequest = (
    companyId: string,
    requestId: string,
    body: { approve: boolean; note?: string }
) =>
    companyRequest<AllowanceRequestRow>(
        `/api/company/${companyId}/allowance-requests/${requestId}/decide`,
        { method: "POST", body: JSON.stringify(body) }
    );

export const getCompanyPolicy = (companyId: string) =>
    companyRequest<CorporatePolicy | Record<string, never>>(`/api/company/${companyId}/policy`);

export const patchCompanyPolicy = (
    companyId: string,
    body: Partial<Omit<CorporatePolicy, "id" | "company_id">>
) =>
    companyRequest<CorporatePolicy>(`/api/company/${companyId}/policy`, {
        method: "PATCH",
        body: JSON.stringify(body),
    });

export const listAllowedDomains = (companyId: string) =>
    companyRequest<AllowedDomainRow[]>(`/api/company/${companyId}/allowed-domains`);

export const addAllowedDomain = (companyId: string, domain: string) =>
    companyRequest<AllowedDomainRow>(`/api/company/${companyId}/allowed-domains`, {
        method: "POST",
        body: JSON.stringify({ domain }),
    });

export const removeAllowedDomain = (companyId: string, domain: string) =>
    companyRequest<{ status: string }>(
        `/api/company/${companyId}/allowed-domains/${encodeURIComponent(domain)}`,
        { method: "DELETE" }
    );

export const getCompanyBillingSummary = (companyId: string, month?: string) =>
    companyRequest<BillingSummary>(
        `/api/company/${companyId}/billing/summary${month ? `?month=${encodeURIComponent(month)}` : ""}`
    );

export const getCompanyBillingStatement = (companyId: string, month: string) =>
    companyRequest<BillingStatement>(
        `/api/company/${companyId}/billing/statements/${encodeURIComponent(month)}`
    );

export const getCompanyBillingTransactions = (companyId: string, skip = 0, limit = 50) =>
    companyRequest<BillingTransactionsPage>(
        `/api/company/${companyId}/billing/transactions?skip=${skip}&limit=${limit}`
    );

export interface PortalVehicleType {
    id: string;
    name: string;
    capacity?: number;
    is_active?: boolean;
}

export const getPortalVehicleTypes = async (): Promise<PortalVehicleType[]> => {
    // Public endpoint — no auth needed, but routing through companyRequest
    // keeps error shapes consistent.
    return companyRequest<PortalVehicleType[]>("/api/v1/vehicle-types");
};
