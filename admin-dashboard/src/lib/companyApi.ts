// Company-portal API client (Spinr for Business).
//
// Same relative-URL/proxy contract as lib/api.ts, but bound to the
// companyAuthStore rider session: 401s silent-refresh via the HttpOnly
// refresh cookie and bounce to /company-login (never the staff /login).
// lib/api.ts's request() delegates any /api/company/* or
// /api/rider/work-profile* path here, so the existing portal pages keep
// their imports untouched.

import { useCompanyAuthStore, CompanyMembershipProfile } from "@/store/companyAuthStore";

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
            const newToken = useCompanyAuthStore.getState().token;
            const retryHeaders = { ...headers, Authorization: `Bearer ${newToken}` };
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
    const res = await fetch("/api/v1/auth/send-otp", {
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
    user: { id: string; phone?: string; first_name?: string; last_name?: string };
}

export const verifyCompanyOtp = async (phone: string, code: string): Promise<CompanyOtpVerifyResult> => {
    const res = await fetch("/api/v1/auth/verify-otp", {
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
